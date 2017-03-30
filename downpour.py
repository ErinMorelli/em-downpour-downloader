#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Copyright (c) 2017 Erin Morelli.

Title       : EM Downpour Downloader
Author      : Erin Morelli
Email       : erin@erinmorelli.com
License     : MIT
Version     : 0.1
"""

# Future
from __future__ import print_function

# Built-ins
import os
import re
import sys
import json
import codecs
import pickle
import argparse
from datetime import datetime, timedelta

# Third-party
import wget
import yaml
import requests
from lxml import html
from tabulate import tabulate
from requests.packages import urllib3
from requests_cache import CachedSession

# Script credits
__title__ = 'EM Downpour Downloader'
__copyright__ = 'Copyright (c) 2017, Erin Morelli'
__author__ = 'Erin Morelli'
__email__ = 'erin@erinmorelli.com'
__license__ = 'MIT'
__version__ = '0.1'

# Disable SSL warnings
urllib3.disable_warnings()

# Set up UTF-8 encoding for Python 2
if sys.version_info[0] < 3:
    __writer__ = codecs.getwriter('utf8')
    sys.stdout = __writer__(sys.stdout)


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
        self.downpour = {}
        self.downpour['root'] = 'https://www.downpour.com/{0}'
        self.downpour['login_url'] = self.downpour['root'].format(
            'customerportal/account/loginPost'
        )
        self.downpour['ajax_root'] = self.downpour['root'].format(
            'blackstone_custom/ajax/{0}'
        )
        self.downpour['filetypes'] = ['m4b', 'mp3']

        # Get user config settings
        self.config = None
        self._load_config()

        # Check download folder read/write access
        self._check_download_folder()

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
                '.downpour_cache_v{major}{minor}'.format(
                    major=sys.version_info[0],
                    minor=sys.version_info[1]
                )
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
        self._load_requests_cache()

        # Login to Downpour and load session cookie jar
        self._load_cookie_jar()

    def _get_argparser(self):
        """Configure and return argument parser.

        Returns:
            DownpourArgumentParser: Command-line argument parser object

        """
        action_help = 'commands:\n'

        # Set up command choices help
        for choice in self._script_actions.keys():
            action_help += '  {key:21} {desc}\n'.format(
                key=choice,
                desc=self._script_actions[choice]
            )

        # Set up script usage and help output
        help_output = '{usage}\n\n{action_help}'.format(
            usage='%(prog)s <command> [book ID(s)] [options]',
            action_help=action_help
        )

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
            help='{0} [{1}].'.format(
                'specify which audiobook filetype to download',
                ', '.join(self.downpour['filetypes'])
            ),
            choices=self.downpour['filetypes']
        )

        # Return argument parser
        return argparser

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

            # Check for JSON output flag
            if self._args['json']:
                self.output = self.__class__.JSON

    def _load_config(self):
        """Load the user config file data into instance variable."""
        self.config = yaml.load(open(self.config_file).read())

        # Set required fields
        required_fields = ['username', 'password', 'folder', 'filetype']

        # Check for required fields
        for required in required_fields:
            if (required not in self.config.keys() or
                    self.config[required] is None):
                error = "Error: configuration field '{0}' is not defined"
                sys.exit(error.format(required))

        # Check that file type is valid
        if self.config['filetype'] not in self.downpour['filetypes']:
            error = "Error: configuration field 'filetype' must be one of: {0}"
            sys.exit(error.format(', '.join(self.downpour['filetypes'])))

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

        # Clear session cache
        if refresh:
            self.session.cache.clear()

    def _get_cookies(self):
        """Login to Downpour and retrieve user session cookies.

        Returns:
            RequestsCookieJar: Requests session cookie jar object from Downpour

        """
        self.session.get(self.downpour['root'].format(None))

        # Login to Downpour
        self.session.post(
            self.downpour['login_url'],
            data={
                'login[username]': self.config['username'],
                'login[password]': self.config['password']
            }
        )

        # Set login error
        login_error = 'Error: unable to login to Downpour'

        # Attempt to retrieve user library
        library = self.session.get(
            self.downpour['ajax_root'].format('ajaxGetCurrentCustomerLibrary')
        )

        # Check that login request was successful
        try:
            books = library.json()
        except ValueError:
            sys.exit(login_error)
        else:
            if (not isinstance(books, dict) or
                    'error' in books.keys() or
                    'library' not in books.keys()):
                sys.exit(login_error)

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

    def _check_download_folder(self):
        """Check that download folder exists and is writable."""
        folder = self.config['folder_abs']

        # Check that folder exists
        if not os.path.exists(folder):
            error = 'Error: folder does not exist: {0}'
            sys.exit(error.format(folder))

        # Check that directory is writable
        if not os.access(folder, os.W_OK or os.R_OK):
            error = 'Error: folder does not have read/write permissions: {0}'
            sys.exit(error.format(folder))

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

        # If we want a non-CLI response, stop here
        if output is self.__class__.RAW:
            return books
        elif output is self.__class__.JSON:
            return json.dumps(books)

        # Set up table headers
        table_headers = [
            'ID',
            'Title',
            'Author',
            'Narrator',
            'Runtime',
            'Purchased'
        ]

        # Set up table display
        table_data = []

        # Format book data
        for book in books:
            # Parse purchase date as datetime object
            purchase_date = datetime.strptime(
                book['purchase_date_clean'], '%m-%d-%y')

            # Set up table row
            table_data.append([
                book['book_id'],
                truncate(book['title']),
                truncate(', '.join(book['author'].split('|'))),
                truncate(', '.join(book['narrator'].split('|'))),
                '{0} hr'.format(book['runtime']),
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
        if output is self.__class__.RAW:
            return book
        elif output is self.__class__.JSON:
            return json.dumps(book)

        # Get purchase date as datetime object
        purchase_date = datetime.strptime(
            book['purchase_date_clean'], '%m-%d-%y')

        # Set output formatting
        form = u'{0:>15}: {1}'

        # Format book data
        book_data = [
            form.format(
                'ID', book['book_id']),
            form.format(
                'Title', book['title']),
            form.format(
                'Author(s)', ', '.join(book['author'].split('|'))),
            form.format(
                'Narrator(s)', ', '.join(book['narrator'].split('|'))),
            form.format(
                'Runtime', '{0} hours'.format(book['runtime'])),
            form.format(
                'Purchase Date', purchase_date.strftime('%d %B %Y')),
            form.format(
                'Rental', 'Yes' if book['rental'] else 'No'),
            form.format(
                'Downloadable', 'Yes' if book['downloadable'] else 'No'),
            form.format(
                'DRM', 'Yes' if book['drm_required'] else 'No'),
            form.format(
                'Link',
                self.downpour['root'].format(book['product_url'][1:])
            )
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

        # Iterate over book IDs to download
        for idx, book_id in enumerate(book_ids):
            # Print new line between books
            if idx and output is self.__class__.CLI:
                print('\n', file=sys.stdout)

            # Download selected book
            self.download_book(book_id)

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
            sys.exit("Error: invalid action: '{0}' (choose from {1})".format(
                action,
                ', '.join(self._script_actions.keys())
            ))

        # Get function to perform action
        action_func = '_do_action_{action}'.format(action=action)

        # Do action
        if hasattr(self, action_func):
            return getattr(self, action_func)()

    def get_library(self):
        """Retrieve list of user library books from Downpour.

        Returns:
            dict: Parsed JSON data from API response

        """
        library = self.session.get(
            self.downpour['ajax_root'].format('ajaxGetCurrentCustomerLibrary')
        )

        # Get books
        books = library.json()

        # Set up book id array
        book_ids = [b['book_id'] for b in books['library']]

        # Get additional library data
        library_data = self.session.post(
            self.downpour['ajax_root'].format('ajaxGetProductDataBrief'),
            data={
                'bookIds[]': book_ids
            }
        )

        # Get books data
        books_data = library_data.json()

        # Merge the two arrays
        for book in books['library']:
            for book_data in books_data:
                if book_data['book_id'] == book['book_id']:
                    book.update(book_data)

        # Return complete book list
        return books['library']

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
        ajax_root = self.downpour['ajax_root']

        # Make request to get book files download information
        dl_data = self.session.get(
            ajax_root.format('ajaxGetCurrentCustomerLibraryDownloadBox'),
            params={
                'id': book['library_item_id']
            }
        )

        # Get HTML
        dl_html = dl_data.json()['html']

        # Parse HTML
        tree = html.fromstring(dl_html)

        # Set up xpath search string
        dl_search = '//{row}/{cell}/div[1]'.format(
            row="tr[{0} and {1}]".format(
                "contains(@class, '{0}')".format(self.config['filetype']),
                "contains(@class, 'file')"
            ),
            cell='td[@class="download"]'
        )

        # Look for download links
        downloads = tree.xpath(dl_search)

        # Set up onclick regex
        onclick_regex = r"^bsa_cl_dl_file\(event, '(\w+)', '(\w+)'\);$"

        # Set up file list
        dl_file_data = []

        # Get download information
        for download in downloads:
            # Get JS onclick action call
            onclick = download.attrib['onclick']

            # Parse the string
            match = re.match(onclick_regex, onclick, re.I)

            # Append to files list
            if match is not None:
                dl_file_data.append({
                    'linkId': match.group(1),
                    'itemId': match.group(2)
                })

        # Return list of files to download
        return dl_file_data

    def get_download_url(self, file_info):
        """Retrieve Downpour book file download URL.

        Args:
            file_info (dict): File part information
                Retrieved from API call in `get_book_file_data`

        Returns:
            str: Download URL for book part file

        """
        dl_url = requests.get(  # Not a cached request as the URL expires
            self.downpour['ajax_root'].format('ajaxGetDLSignedUrl'),
            cookies=self.session.cookies,
            params=file_info
        )

        # Get JSON response
        dl_json = dl_url.json()

        # Return download URL
        return dl_json['url']

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

            # Convert str to unicode if this is Python 2
            if sys.version_info[0] < 3:
                template = unicode(self.config['template'], 'utf-8')
            else:
                template = self.config['template']

        # Format folder path from template
        book_folder = template.format(
            title=book['title'],
            author=', '.join(book['author'].split('|')),
            narrator=', '.join(book['narrator'].split('|')),
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
            if output is self.__class__.CLI:
                print(
                    "Warning: file '%s' already exists, skipping" % file_path,
                    file=sys.stderr
                )
            return

        # Get download URL
        file_url = self.get_download_url(file_data)

        # Set download progress bar
        bar_style = wget.bar_adaptive if output is self.__class__.CLI else None

        # Download file
        temp_file_path = wget.download(
            file_url,
            bar=bar_style,
            out=os.path.dirname(file_path)
        )

        # Set error message:
        error = 'Error: there was a problem downloading the file: {0}'

        # Check that the file was downloaded
        if not os.path.isfile(temp_file_path):
            sys.exit(error.format(file_path))

        # Rename the file
        os.rename(temp_file_path, file_path)

        # Check that the file was renamed
        if not os.path.isfile(file_path):
            sys.exit(error.format(file_path))

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
        if output is self.__class__.CLI:
            print(
                u'== "{title}" by {author} ==\n+ Path: {path}'.format(
                    title=book['title'],
                    author=u', '.join(book['author'].split('|')),
                    path=book_path
                ),
                file=sys.stdout
            )

        # Download each book part
        for idx, file_data in enumerate(book_file_data):
            # Get file part
            file_part = ', Part {0}'.format(idx + 1) if parts > 1 else ''

            # Get file name
            file_name = '{book_title}{file_part}.{file_type}'.format(
                book_title=book['title'],
                file_part=file_part,
                file_type=self.config['filetype']
            )

            # Set file path
            file_path = os.path.join(book_path, file_name)

            # Print status update
            if output is self.__class__.CLI:
                print(
                    '+ File [{part} of {parts}]: "{name}"'.format(
                        part=idx + 1,
                        parts=parts,
                        name=file_name
                    ),
                    file=sys.stdout
                )

            # Download the file
            self.download_book_file(file_data, file_path)

            # Add book to list
            downloaded_files.append(file_path)

        # Return downloaded files
        if output is self.__class__.RAW:
            return downloaded_files
        elif output is self.__class__.JSON:
            return json.dumps({'files': downloaded_files})
        elif output is self.CLI:
            print('+ Done.', file=sys.stdout)


class ScriptAction(argparse.Action):
    """Custom script validation action for argparse."""

    def __call__(self, argparser, namespace, values, option_string=None):
        """Check that action has book IDs, if-needed."""
        if not len(values) and namespace.action in ['download', 'book']:
            argparser.error(
                'Missing book ID{s} for {action}'.format(
                    action=namespace.action,
                    s='(s)' if namespace.action == 'download' else ''
                )
            )

        # Set value in namespace object
        setattr(namespace, self.dest, values)


class FileAction(argparse.Action):
    """Custom files validation action for argparse."""

    def __call__(self, argparser, namespace, values, option_string=None):
        """Check that file provided exists."""
        file_path = os.path.abspath(values)

        # Check that file exists
        if not os.path.exists(file_path):
            error = 'Path provided for {0} {1} {2}'.format(
                self.dest, 'does not exist:', values)
            argparser.error(error)

        # Set value in namespace object
        setattr(namespace, self.dest, values)


class DownpourArgumentParser(argparse.ArgumentParser):
    """Custom command-line argument parser for argparse."""

    def error(self, message):
        """Display a simple help message via stdout before error messages."""
        sys.stdout.write('Use `%s --help` to view more options\n' % self.prog)

        # Display error message and exit
        sys.exit('Error: {0}'.format(message))

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
    return '{title} v{version}{extra}\n'.format(
        title=__title__,
        version=__version__,
        extra=' / by {author} <{email}>\n'.format(
            author=__author__,
            email=__email__
        ) if extra else ''
    )


def truncate(string, length=16):
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
    # Connect to Downpour object
    EDD = EMDownpourDownloader(output=EMDownpourDownloader.CLI)

    # Get output from main function
    __output__ = EDD.do_action()

    # Check for returned output
    if __output__ is not None:
        print(__output__, file=sys.stdout)
