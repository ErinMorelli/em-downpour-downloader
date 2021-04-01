#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Copyright (c) 2017-2021, Erin Morelli.

Title       : EM Downpour Downloader
Author      : Erin Morelli
Email       : me@erin.dev
License     : MIT
Version     : 0.3
"""

# Built-ins
import os
import re
import sys
import json
import pickle
import argparse
from datetime import datetime, timedelta

# Third-party
import yaml
import requests
import animation
from bs4 import BeautifulSoup
from tabulate import tabulate
from clint.textui import progress
from requests.packages import urllib3
from requests_cache import CachedSession

# Script credits
__title__ = 'EM Downpour Downloader'
__copyright__ = 'Copyright (c) 2017-2021, Erin Morelli'
__author__ = 'Erin Morelli'
__email__ = 'me@erin.dev'
__license__ = 'MIT'
__version__ = '0.3'

# Disable SSL warnings
urllib3.disable_warnings()


class EMDownpourDownloader(object):
    """Class to interact with the Downpour API.

    Attributes:
        RAW (int): Constant value for outputting raw (python) responses
        JSON (int): Constant value for outputting JSON responses
        CLI (int): Constant value for outputting command-line responses

    """

    # Set output types
    RAW = 1
    JSON = 2
    CLI = 3

    def __init__(self, output=RAW):
        """Initialize class and connect to the Downpour API.

        Args:
            output (int): Output type identifier

        """
        self._root_dir = os.path.dirname(os.path.realpath(__file__))

        # Get local user directory
        self.local_dir = os.path.join(
            os.path.expanduser('~'), '.config', 'downpour-downloader')

        # Get config file
        self.config_file = os.path.join(self.local_dir, 'config.yml')

        # Set Downpour connection info
        self.downpour = dict()
        self.downpour['root'] = 'https://www.downpour.com/{0}'
        self.downpour['filetypes'] = ['m4b', 'mp3']
        self.downpour['headers'] = {
            'User-Agent': f'{__title__}/{__version__}'
        }

        # Get user config settings
        self.config = None
        self._load_config()

        # Check download folder read/write access
        self._check_folder_permissions(self.config['folder_abs'])

        # Set requests caching data
        self._cache = {
            'pickle_protocol': 2,
            'cookie_expire_default': {
                'hours': 1
            },
            'cookies_file': os.path.join(self.local_dir, '.downpour_cookies'),
            'expire_default': 3600,  # 1 hour in seconds
            'file': os.path.join(
                self.local_dir,
                f'.downpour_cache_v{sys.version_info[0]}{sys.version_info[1]}'
            )
        }

        # Update defaults from config
        for update_key in ['cookie_expire_default', 'expire_default']:
            if (update_key in self.config.keys() and
                    self.config[update_key] is not None):
                self._cache[update_key] = self.config[update_key]

        # Set up script
        self._script_actions = {
            'library': 'lists all books available to download',
            'book': 'get information about a given book by ID',
            'download': 'downloads book(s) by ID'
        }

        # Set output type
        self.output = output

        # Handle command-line arguments
        self._args = None
        self._load_args()

        # Load cache and initialize requests session
        self.session = None
        self._load_requests_cache(refresh=self._args['refresh'])

        # Login to Downpour and load session cookie jar
        self._load_cookie_jar(refresh=self._args['refresh'])

    def _get_argparser(self):
        """Configure and return argument parser.

        Returns:
            DownpourArgumentParser: Command-line argument parser object

        """
        action_help = 'commands:\n'

        # Set up command choices help
        for choice in self._script_actions.keys():
            action_help += f'  {choice:21} {self._script_actions[choice]}\n'

        # Set up script usage and help output
        help_output = f'%(prog)s <command> [book ID(s)] [options]' \
                      f'\n\n{action_help}'

        # Set up argument parser
        argparser = DownpourArgumentParser(
            formatter_class=argparse.RawTextHelpFormatter,
            usage=help_output
        )

        # Positional arguments
        argparser.add_argument(
            'action',
            metavar='command',
            help=argparse.SUPPRESS,
            choices=self._script_actions.keys()
        )
        argparser.add_argument(
            'book_ids',
            nargs='*',
            help=argparse.SUPPRESS,
            action=ScriptAction
        )

        # Optional flags
        argparser.add_argument(
            '-v', '--version',
            action='version',
            version=get_version(False)
        )
        argparser.add_argument(
            '-j', '--json',
            default=False,
            help='prints responses as JSON',
            action='store_true'
        )
        argparser.add_argument(
            '-r', '--refresh',
            default=False,
            help='force a refresh of cached library data',
            action='store_true'
        )
        argparser.add_argument(
            '-f', '--folder',
            default=self.config['folder'],
            help='specify a folder for files to be downloaded to',
            action=FileAction
        )
        argparser.add_argument(
            '-t', '--filetype',
            metavar='FILETYPE',
            default=self.config['filetype'],
            help=f'specify which audiobook filetype to download '
                 f'[{", ".join(self.downpour["filetypes"])}].',
            choices=self.downpour['filetypes']
        )
        argparser.add_argument(
            '-d', '--desc',
            help='sort library in descending order by purchase date',
            action='store_true'
        )
        argparser.add_argument(
            '-c', '--count',
            help='specify number of books to return from library',
            type=int
        )

        # Return argument parser
        return argparser
    
    def _error(self, message, reason=None) -> object:
        """??"""
        error = f'ERROR: {message}'
        if reason:
            error += f' - {reason}'
        sys.exit(error)

    def _load_args(self):
        """Parse and load command-line arguments."""
        if __name__ != '__main__':
            return

        # Get argument parser
        argparser = self._get_argparser()

        # Check for no args and print help
        if len(sys.argv) == 1:
            argparser.print_help()
            sys.exit(1)

        # Parse arguments
        args = argparser.parse_args()

        # Store arguments as dict
        self._args = args.__dict__

        # Update instance with parsed argument data
        if self._args is not None:

            # Merge arguments with config
            for config_key in self.config.keys():
                if config_key in self._args.keys():
                    self.config[config_key] = self._args[config_key]

            # Always refresh for download requests
            if self._args['action'] == 'download':
                self._args['refresh'] = True

            # Check for JSON output flag
            if self._args['json']:
                self.output = self.JSON

    def _load_config(self):
        """Load the user config file data into instance variable."""
        self.config = yaml.load(open(self.config_file).read(),
                                Loader=yaml.FullLoader)

        # Set required fields
        required_fields = ['username', 'password', 'folder', 'filetype']

        # Check for required fields
        for required in required_fields:
            if (required not in self.config.keys() or
                    self.config[required] is None):
                self._error(f'config field "{required}" is not defined')

        # Check that file type is valid
        if self.config['filetype'] not in self.downpour['filetypes']:
            types = ', '.join(self.downpour['filetypes'])
            self._error(f'config field "filetype" must be one of: {types}')

        # Parse config folder path
        self.config['folder_abs'] = os.path.abspath(
            os.path.expanduser(self.config['folder']))

    def _load_requests_cache(self, refresh=False):
        """Load the requests cache module with user settings.

        Args:
            refresh (bool, optional): Force a refresh of cached requests data

        """
        if ('expire_default' in self.config.keys() and
                self.config['expire_default'] is not None):
            expire_after = self.config['expire_default']
        else:
            # Fallback to default expiration
            expire_after = self._cache['expire_default']

        # Set up requests session cache
        self.session = CachedSession(
            self._cache['file'],
            expire_after=expire_after,
            allowable_methods=('GET', 'POST')
        )

        # Set requests session headers
        self.session.headers = self.downpour['headers']

        # Clear session cache
        if refresh:
            self.session.cache.clear()

    def _get_cookies(self):
        """Login to Downpour and retrieve user session cookies.

        Returns:
            RequestsCookieJar: Requests session cookie jar object from Downpour

        """
        login_error = 'unable to login to Downpour'

        # Visit Downpour home page
        home = self.session.get(self.downpour['root'].format(''))
        home_soup = BeautifulSoup(home.text, 'html.parser')

        # Look for login URL
        login_link = home_soup.find('a', string='Sign In')
        login_url = login_link['href']
        if login_url is None:
            self._error(login_error, reason='cannot find sign in link')

        # Navigate to login page
        post = self.session.get(login_url)
        post_soup = BeautifulSoup(post.text, 'html.parser')

        # Look for post URL
        login_form = post_soup.find('form', id='login-form')
        post_url = login_form['action']
        if post_url is None:
            self._error(login_error, reason='cannot find login form action')

        # Look for form key
        form_key_input = post_soup.find('input', attrs={'name': 'form_key'})
        form_key = form_key_input['value']
        if form_key is None:
            self._error(login_error, reason='cannot find login form key')

        # Login to Downpour
        login = requests.post(
            post_url,
            data={
                'form_key': form_key,
                'login[username]': self.config['username'],
                'login[password]': self.config['password'],
                'send': ''
            },
            cookies=self.session.cookies,
            headers=self.downpour['headers']
        )

        # Attempt to retrieve user library
        library = self.session.get(
            self.downpour['root'].format('my-library'),
            cookies=login.cookies
        )
        library_soup = BeautifulSoup(library.text, 'html.parser')

        # Look for logout URL
        if not library_soup.find('a', text='Signout'):
            self._error(login_error, reason='cannot find sign out link')

        # Return user cookies
        return library.cookies

    def _store_cookies(self, cookies):
        """Store user cookies to local cache file.

        Args:
            RequestsCookieJar: Requests session cookie jar object from Downpour

        """
        pickle.dump(
            cookies,
            open(self._cache['cookies_file'], 'wb+'),
            protocol=self._cache['pickle_protocol']
        )

    def _load_cookies(self):
        """Load user cookies from local cache file."""
        return pickle.load(open(self._cache['cookies_file'], 'rb'))

    def _fill_cookie_jar(self, cookies):
        """Load cached cookies into instance Requests cookie jar.

        Args:
            RequestsCookieJar: Requests session cookie jar object from Downpour

        """
        self.session.cookies.update(cookies)

    def _cookies_expired(self, cookies):
        """Check if user cookies have expired.

        Args:
            RequestsCookieJar: Requests session cookie jar object from Downpour

        Retuns:
            bool: True if any required cookies have expired, else False

        """
        now = datetime.now()

        # Get modification time of cookie file
        mod_time = datetime.fromtimestamp(
            os.path.getmtime(self._cache['cookies_file'])
        )

        # Get refresh time from config
        if ('cookie_cache' in self.config.keys() and
                self.config['cookie_cache'].keys()):
            refresh = timedelta(**self.config['cookie_cache'])
        else:
            # Set default refresh time
            refresh = timedelta(**self._cache['cookie_expire_default'])

        # Check cookie file modification time
        if now - mod_time > refresh:
            return True

        # Check on Downpour cookie expiration times
        for cookie in cookies:
            # Get cookie expiration
            expires = datetime.fromtimestamp(cookie.expires)

            # Exit if cookie has expired
            if now > expires:
                return True

        # Return not expired
        return False

    def _load_cookie_jar(self, refresh=False):
        """Retrieve cookies from local cache or new from Downpour.

        Args:
            refresh (bool, optional): Force a refresh of cached cookie data

        """
        if not refresh:
            try:
                cookies = self._load_cookies()
            except IOError:
                refresh = True
            else:
                # Check for missing or expired cookies
                if not cookies or self._cookies_expired(cookies):
                    refresh = True

        # Get new cookies
        if refresh:
            # Retrieve cookies from Downpour
            cookies = self._get_cookies()

            # Store new cookies
            self._store_cookies(cookies)

        # Fill the cookie jar
        self._fill_cookie_jar(cookies)

    def _check_folder_permissions(self, folder):
        """Check that folder exists and is writable."""
        if not os.path.exists(folder):
            self._error(f'folder does not exist: {folder}')

        # Check that directory is readable and writable
        if not os.access(folder, os.W_OK or os.R_OK):
            self._error(f'unable read/write folder: {folder}')

    def _do_action_library(self, output=None):
        """Retrieve a list of the user's Downpour library books.

        Args:
            output (int, optional): Override the class-level output type

        Returns:
            dict, str: Library book data, depending on output type
                Can be dict, JSON, or formatted ascii table

        """
        if output is None:
            output = self.output

        # Get books from Downpour
        books = self.get_library()

        # Handle CLI args
        if self._args['desc']:
            books = list(reversed(books))
        if self._args['count']:
            books = books[:self._args['count']]

        # If we want a non-CLI response, stop here
        if output is self.RAW:
            return books
        elif output is self.JSON:
            return json.dumps(books)

        # Set up table headers
        table_headers = [
            'ID',
            'Title',
            'Author',
            'Runtime',
            'Purchased'
        ]

        # Set up table display
        table_data = []

        # Format book data
        for book in books:
            # Parse purchase date as datetime object
            purchase_date = datetime.strptime(
                book['purchase_date_string'], '%Y-%m-%d')

            # Set up table row
            table_data.append([
                book['book_id'],
                truncate(book['title']),
                truncate(', '.join(book['author'].split('|'))),
                f'{book["runtime"]} hr',
                purchase_date.strftime('%d %b %y')
            ])

        # Return formatted and UTF-8 encoded table
        return tabulate(
            table_data,
            headers=table_headers,
            tablefmt="psql"
        )

    def _do_action_book(self, book_id=None, output=None):
        """Retrieve and display information about a specific Downpour book.

        Args:
            book_id (str, optional): Downpour book ID
                Defaults to first value parsed from command-line `book_ids`
            output (int, optional): Override the class-level output type

        Returns:
            dict, str: Single library book data, depending on output type
                Can be dict, JSON, or formatted ascii text

        """
        if book_id is None:
            book_id = self._args['book_ids'][0]
        if output is None:
            output = self.output

        # Get book
        book = self.get_book_by_id(book_id)

        # If we want a non-CLI response, stop here
        if output is self.RAW:
            return book
        elif output is self.JSON:
            return json.dumps(book)

        # Get purchase date as datetime object
        purchase_date = datetime.strptime(
            book['purchase_date_string'], '%Y-%m-%d')

        # Set output formatting
        form = u'{0:>15}: {1}'

        # Format book data
        book_data = [
            form.format('ID', book['book_id']),
            form.format('Title', book['title']),
            form.format('Author(s)', ', '.join(book['author'].split('|'))),
            form.format('Runtime', f'{book["runtime"]} hours'),
            form.format('Purchase Date', purchase_date.strftime('%d %B %Y')),
            form.format('Released', 'Yes' if book['is_released'] else 'No'),
            form.format('Rental', 'Yes' if book['is_rental'] else 'No'),
            form.format('DRM', 'Yes' if book['drm'] else 'No'),
            form.format('Link', book['link'])
        ]

        # Return formatted book data
        return '\n'.join(book_data)

    def _do_action_download(self, book_ids=None, output=None):
        """Download book(s) from Downpour.

        Args:
            book_ids (list, optional): List of Downpour book IDs
                Defaults to values parsed from command-line `book_ids`
            output (int, optional): Override the class-level output type

        """
        if book_ids is None:
            book_ids = self._args['book_ids']
        if output is None:
            output = self.output

        # Track downloaded books
        downloaded_books = {}

        # Iterate over book IDs to download
        for idx, book_id in enumerate(book_ids):
            # Print new line between books
            if idx and output is self.CLI:
                print('\n', file=sys.stdout)

            # Download selected book
            downloaded_books[book_id] = self.download_book(book_id)

        # Output formatted response
        if output is self.RAW:
            return downloaded_books
        elif output is self.JSON:
            return json.dumps(downloaded_books)

    def do_action(self, action=None):
        """Wrapper function to perform a specific action.

        Args:
            action (str, optional): Name of the action to perform
                Defaults to parsed command-line value `command`

        """
        if action is None:
            action = self._args['action']

        # Check for valid action
        if action not in self._script_actions.keys():
            choices = ', '.join(self._script_actions.keys())
            self._error(f'invalid action: "{action}" (choose from {choices})')

        # Get function to perform action
        action_func = f'_do_action_{action}'

        # Do action
        if hasattr(self, action_func):
            return getattr(self, action_func)()

    def get_library(self):
        """Retrieve list of user library books from Downpour.

        Returns:
            dict: Parsed JSON data from API response

        """
        library = self.session.get(self.downpour['root'].format('my-library'))

        # Parse HTML
        soup = BeautifulSoup(library.text, 'html.parser')

        # Find book data
        books_html = soup.find_all(
            'span',
            attrs={'class': 'product-library-item-link'}
        )

        # Populate book list
        books = []
        for book in books_html:
            attrs = book.attrs
            runtime = attrs['data-runtime']
            books.append({
                'author': attrs['data-author-display-string'],
                'book_id': attrs['data-book_id'],
                'drm': attrs['data-drm'] == '1',
                'expiration': attrs['data-expiration'],
                'is_released': attrs['data-is-released'] == '1',
                'is_rental': attrs['data-is-rental'] == '1',
                'itemid': attrs['data-itemid'],
                'purchase_date': attrs['data-purchase-date'],
                'purchase_date_string': attrs['data-purchase-date-string'],
                'release_date': attrs['data-release-date'],
                'remaining': attrs['data-remaining-string'],
                'runtime': 0 if runtime == '' else float(runtime),
                'sku': attrs['data-sku'],
                'link': attrs['data-href'],
                'title': attrs['title'],
                'cover': book.find('img').attrs['src']
            })

        # Return complete book list
        return books

    def get_book_by_id(self, book_id):
        """Retrieve book from user Downpour library by book ID.

        Args:
            book_id (str): Downpour book ID

        Returns:
            dict: Parsed JSON data from API response

        """
        books = self.get_library()

        # Find book in library
        return next(book for book in books if book['book_id'] == book_id)

    def get_book_file_data(self, book):
        """Retrieve additional file information from Downpour.

        Args:
            book (dict): Downpour book data

        Returns:
            list: List of file part data for making download requests

        """
        dp_root = self.downpour['root']

        # Make request to get book files download information
        dl_data = self.session.post(
            dp_root.format('my-library/ajax/ajaxGetBookActionOptions'),
            data={'bookId': book['book_id']},
            cookies=self.session.cookies
        )

        # Get JSON
        dl_json = dl_data.json()
        if not dl_json['status']:
            self._error('could not retrieve book download manifest')

        # Get manifest
        manifest = dl_json['manifest']

        # Set up file regexes
        file_regex = f'\.{self.config["filetype"]}$'
        file_part_regex = r'^File (\d+) of \d+$'

        # Return only correct file type
        files = []
        for file_name in manifest.keys():
            if re.search(file_regex, file_name, re.I):
                file = manifest[file_name]

                # Parse file part number
                part = re.match(file_part_regex, file['countOf'], re.I)
                if not part:
                    self._error('could not parse book download part')

                # Set file part number
                file['part'] = int(part.group(1))

                # Add to files list
                files.append(file)

        # Sort files by part number
        sorted_files = sorted(files, key=lambda k: k['part'], reverse=False)

        # Return sorted file list
        return sorted_files

    def get_download_url(self, file_info):
        """Retrieve Downpour book file download URL.

        Args:
            file_info (dict): File part information
                Retrieved from API call in `get_book_file_data`

        Returns:
            str: Download URL for book part file

        """
        dl_url = self.session.post(  # Not a cached request as the URL expires
            self.downpour['root'].format('my-library/ajax/ajaxDLBookBD'),
            cookies=self.session.cookies,
            data={
                'bdfile': file_info['filename'],
                'niceName': file_info['prettyName']
            }
        )

        # Get JSON response
        dl_json = dl_url.json()
        if not dl_json['status']:
            self._error('could not retrieve the book download URL(s)')

        # Return download URL
        return dl_json['link']

    def get_book_path(self, book):
        """Get and create the download file path for a book.

        Args:
            book (dict): Downpour book information

        Returns:
            str: Absolute path to book download target folder

        """
        template = u'{author}/{title}'

        # Check for user-specified template
        if ('template' in self.config.keys() and
                self.config['template'] is not None):
            template = self.config['template']

        # Format folder path from template
        book_folder = template.format(
            title=book['title'],
            author=', '.join(book['author'].split('|')),
            book_id=book['book_id']
        )

        # Join book folder to user folder
        book_path = os.path.join(self.config['folder_abs'], book_folder)

        # Create folders if they don't exist
        if not os.path.exists(book_path):
            os.makedirs(book_path)

        # Return
        return book_path

    def download_book_file(self, file_data, file_path, output=None):
        """Download book part file from Downpour and rename it.

        Args:
            file_data (dict): File part information
                Retrieved from API call in `get_book_file_data`
            file_path (str): Absolute path to download target file
            output (int, optional): Override the class-level output type

        """
        if output is None:
            output = self.output

        # Exit if this file already exists
        if os.path.isfile(file_path):
            if output is self.CLI:
                print(f'Warning: file "{file_path}" already exists, skipping',
                      file=sys.stderr)
            return

        # Get download URL
        file_url = self.get_download_url(file_data)

        # Get target folder
        out_folder = os.path.dirname(file_path)

        # Check folder permissions
        self._check_folder_permissions(out_folder)

        # Open file download stream
        stream = requests.get(file_url, stream=True)

        # Read and download from file stream
        with open(file_path, 'wb') as handle:
            chunk_size = 1024

            # Determine if we need a progress bar
            if output is self.CLI:
                # Set up progress bar data
                total_length = int(stream.headers.get('content-length'))
                expected_size = (total_length / chunk_size) + 1

                # Set progress bar chunks
                chunks = progress.bar(
                    stream.iter_content(chunk_size=chunk_size),
                    expected_size=expected_size
                )
            else:
                # Use standard, silent stream
                chunks = stream.iter_content(chunk_size=chunk_size)

            # Download file
            for chunk in chunks:
                if chunk:
                    handle.write(chunk)
                    handle.flush()

        # Check that the file was downloaded
        if not os.path.isfile(file_path):
            self._error(f'unable to download file: {file_path}')

    def download_book(self, book_id, output=None):
        """Download all available book part files from Downpour.

        Args:
            book_id (str): Downpour book ID
            output (int, optional): Override the class-level output type

        Returns:
            list, str: Downloaded book data, depending on output type
                Can be list of new files, JSON array, or formatted ascii text

        """
        if output is None:
            output = self.output

        # Get book from library
        book = self.get_book_by_id(book_id)

        # Retrieve book file information
        book_file_data = self.get_book_file_data(book)

        # Count how many book parts
        parts = len(book_file_data)

        # Track downloaded files
        downloaded_files = []

        # Get path to download folder
        book_path = self.get_book_path(book)

        # Print CLI update
        if output is self.CLI:
            print(
                '== "{title}" by {author} ==\n+ Path: {path}'.format(
                    title=book['title'],
                    author=u', '.join(book['author'].split('|')),
                    path=book_path
                ),
                file=sys.stdout
            )

        # Download each book part
        for file_data in book_file_data:
            # Get file part number
            part = file_data['part']

            # Get file part
            file_part = f', Part {part}' if parts > 1 else ''

            # Get file name
            file_name = '{book_title}{file_part}.{file_type}'.format(
                book_title=file_data['title'],
                file_part=file_part,
                file_type=file_data['ext']
            )

            # Set file path
            file_path = os.path.join(book_path, file_name)

            # Print status update
            if output is self.CLI:
                print(f'+ {file_data["countOf"]}: "{file_name}"',
                      file=sys.stdout)

            # Download the file
            self.download_book_file(file_data, file_path)

            # Add book to list
            downloaded_files.append(file_path)

        # Return downloaded files
        if output is self.CLI:
            print('+ Done.', file=sys.stdout)
        else:
            return downloaded_files


class ScriptAction(argparse.Action):
    """Custom script validation action for argparse."""

    def __call__(self, argparser, namespace, values, option_string=None):
        """Check that action has book IDs, if-needed."""
        if not len(values) and namespace.action in ['download', 'book']:
            s = '(s)' if namespace.action == 'download' else ''
            argparser.error(f'Missing book ID{s} for {namespace.action}')

        # Set value in namespace object
        setattr(namespace, self.dest, values)


class FileAction(argparse.Action):
    """Custom files validation action for argparse."""

    def __call__(self, argparser, namespace, values, option_string=None):
        """Check that file provided exists."""
        file_path = os.path.abspath(values)

        # Check that file exists
        if not os.path.exists(file_path):
            error = f'Path provided for {self.dest} does not exist {values}'
            argparser.error(error)

        # Set value in namespace object
        setattr(namespace, self.dest, values)


class DownpourArgumentParser(argparse.ArgumentParser):
    """Custom command-line argument parser for argparse."""

    def error(self, message):
        """Display a simple help message via stdout before error messages."""
        sys.stdout.write(f'Use `{self.prog} --help` to view more options\n')

        # Display error message and exit
        sys.exit(f'ERROR: {message}')

    def print_help(self, files=None):
        """Make the printed help message look nicer.

        Adds padding before and after the text and by adding the program's
        title and version information in the header.
        """
        sys.stdout.write(get_version())

        # Call super
        super(DownpourArgumentParser, self).print_help(files)


def get_version(extra=True):
    """Format program version information.

    Args:
        extra (bool): When True adds extra information to output

    Returns:
        str: Formatted program version information

    """
    extra = f' / by {__author__} <{__email__}>\n' if extra else ''
    return f'{__title__} v{__version__}{extra}\n'


def truncate(string, length=20):
    """Truncate a string and add ellipsis, if-needed.

    Args:
        string (str): String to be truncated
        length (int, optional): Lenth of string to truncate

    Returns:
        str: Truncated string

    """
    ellipsis = '..'

    # Append ellipsis if-needed
    if len(string) > length:
        # Trim string to new length + ellipsis
        trunc = string[0:length-len(ellipsis)].strip()
        trunc += ellipsis
    else:
        # Trim string to new length
        trunc = string[0:length].strip()

    # Return truncated string
    return trunc


# Run the script from the command-line
if __name__ == '__main__':
    # Start animation
    wait = animation.Wait(text='loading')
    wait.start()

    # Connect to Downpour object
    EDD = EMDownpourDownloader(output=EMDownpourDownloader.CLI)

    # Get output from main function
    __output__ = EDD.do_action()

    # Stop animation
    wait.stop()

    # Check for returned output
    if __output__ is not None:
        print(__output__, file=sys.stdout)
