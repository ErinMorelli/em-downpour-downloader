"""
Copyright (C) 2021 Erin Morelli.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see [https://www.gnu.org/licenses/].
"""

import os
import re
import json
from textwrap import shorten
from datetime import datetime, date

import click
import requests
from tqdm import tqdm
from dateutil import parser
from bs4 import BeautifulSoup
from tabulate import tabulate, tabulate_formats

from sqlalchemy.sql import or_
from sqlalchemy_utils.types import URLType
from sqlalchemy import (Table, Column, Date, DateTime, Integer, String,
                        Boolean, Float, func)

from .content import DownpourContent


class BooksContent(DownpourContent):
    """Manage Downpour books."""
    command = 'books'
    model_name = command
    # API endpoints
    api_base = DownpourContent.base_url
    book_meta_url = f'{api_base}/my-library/ajax/ajaxGetBookActionOptions'
    book_dl_url = f'{api_base}/my-library/ajax/ajaxDLBookBD'
    # Set CLI details for videos
    command_help = 'Manage Downpour books.'
    commands = ['list', 'update', 'show', 'download', 'open']

    def _update_books(self, session):
        """Add new books to database."""
        res = requests.get(self.library_url,
                           headers=self.headers,
                           cookies=session)
        soup = BeautifulSoup(res.text, self.parser)

        # Find book data
        all_books = soup.find_all('span', attrs={
            'class': 'product-library-item-link'
        })

        #  Set up progress bar data
        progress_bar = {
            'iterable': all_books,
            'unit': 'books',
            'desc': 'Scanning for new books',
            'bar_format': '{l_bar}{bar}| {n_fmt}/{total_fmt} {unit}'
        }

        # Parse list of books
        added = []
        for dp_book in tqdm(**progress_bar):
            book = self._create_book(dp_book)
            if book:
                added.append(book)
                self.db.add(book)

        # Only commit the changes if anything was added
        if added:
            self.db.commit()

        # Return the list of added books
        return added

    def _find_book(self, book_id):
        """Searches for a book in the database by ID."""
        return self.db.query(self.model)\
            .filter_by(book_id=book_id)\
            .one_or_none()

    def _create_book(self, book):
        """Creates a new video entry in the database."""
        attrs = book.attrs
        book_id = attrs['data-book_id']
        runtime = attrs['data-runtime']

        # Check for existing
        if self._find_book(book_id):
            return None

        # Setup dates
        purchase_date = parser.parse(attrs['data-purchase-date'])
        release_date = parser.parse(attrs['data-release-date']).date()

        # Return the new book object
        return self.model(
            book_id=book_id,
            item_id=attrs['data-itemid'],
            sku=attrs['data-sku'],
            title=attrs['title'],
            author=attrs['data-author-display-string'],
            drm=attrs['data-drm'] == '1',
            is_released=attrs['data-is-released'] == '1',
            is_rental=attrs['data-is-rental'] == '1',
            purchase_date=purchase_date,
            release_date=release_date,
            runtime=0 if runtime == '' else float(runtime),
            url=attrs['data-href'],
            cover=book.find('img').attrs['src']
        )

    def _get_book_file_data(self, account, book, file_type):
        """Get meta data for all book file parts."""
        dl_data = self.session(account.session).post(
            self.book_meta_url,
            data={'bookId': book.book_id}
        )

        # Get JSON
        dl_json = dl_data.json()
        if not dl_json['status']:
            self.manager.error('Could not retrieve book download manifest')
            return None

        # Get manifest
        manifest = dl_json['manifest']

        # Set up file regexes
        file_regex = fr'\.{file_type}$'
        file_part_regex = r'^File (\d+) of \d+$'

        # Return only correct file type
        files = []
        for file_name in manifest.keys():
            if re.search(file_regex, file_name, re.I):
                file = manifest[file_name]

                # Parse file part number
                part = re.match(file_part_regex, file['countOf'], re.I)
                if not part:
                    self.manager.error('Could not parse book download part')

                # Set file part number
                file['part'] = int(part.group(1))

                # Add to files list
                files.append(file)

        # Sort files by part number
        sorted_files = sorted(files, key=lambda k: k['part'], reverse=False)

        # Return sorted file list
        return sorted_files

    def _get_download_url(self, session, file_info):
        """Retrieve Downpour book file download URL."""
        dl_url = requests.post(
            self.book_dl_url,
            headers=self.headers,
            cookies=session,
            data={
                'bdfile': file_info['filename'],
                'niceName': file_info['prettyName']
            }
        )

        # Get JSON response
        dl_json = dl_url.json()
        if not dl_json['status']:
            self.manager.error('Could not retrieve the book download URL(s)')

        # Return download URL
        return dl_json['link']

    @staticmethod
    def _get_book_path(account, book, book_path=None):
        """Get and create the download file path for a book."""
        download_dir = book_path if book_path else account.download_dir

        # Set up folder name from book author and title
        book_folder = account.folder_template.format(
            title=book.title,
            author=', '.join(book.author.split('|')),
            book_id=book.book_id
        )

        # Join book folder to user folder
        book_path = os.path.join(download_dir, book_folder)

        # Create folders if they don't exist
        if not os.path.exists(book_path):
            os.makedirs(book_path)

        # Return
        return book_path

    def _check_folder_permissions(self, folder):
        """Check that folder exists and is writable."""
        if not os.path.exists(folder):
            self.manager.error(f'Folder does not exist: {folder}')
            return False

        # Check that directory is readable and writable
        if not os.access(folder, os.W_OK or os.R_OK):
            self.manager.error(f'Unable read/write folder: {folder}')
            return False

        # Folder has correct permissions
        return True

    def _download_book_file(self, account, file_data, file_path):
        """Download book part file from Downpour and rename it."""
        if os.path.isfile(file_path):
            self.manager.warning(f'File "{file_path}" exists, skipping')
            return

        # Get download URL
        file_url = self._get_download_url(account.session, file_data)

        # Get target folder
        out_folder = os.path.dirname(file_path)

        # Check folder permissions
        if not self._check_folder_permissions(out_folder):
            return

        # Open file download stream
        stream = self.session(account.session).get(file_url, stream=True)

        # Setup download progress bar data
        progress_bar = {
            'miniters': 1,
            'desc': os.path.basename(file_path),
            'total': int(stream.headers.get('content-length', 0))
        }

        # Read and download from file stream
        with tqdm.wrapattr(open(file_path, 'wb'), 'write', **progress_bar) as fh:
            for chunk in stream.iter_content(chunk_size=4096):
                fh.write(chunk)

        # Check that the file was downloaded
        if not os.path.isfile(file_path):
            self.manager.error(f'Unable to download file: {file_path}')

    def _download_book(self, account, book, book_path, yes, file_type=None):
        """Downloads book files to the specified path."""
        file_type = file_type if file_type else account.file_type.value

        # Get book file data from API
        book_file_data = self._get_book_file_data(account, book, file_type)
        if not book_file_data:
            self.manager.error(f'No .{file_type} files found for this book.')
            return

        # Count how many book parts
        parts = len(book_file_data)
        files = 'files' if parts > 1 else 'file'

        # Get path to download folder
        book_path = self._get_book_path(account, book, book_path)
        if not yes:
            if not click.confirm(f'Download {parts} {files} to {book_path}?'):
                return
        else:
            click.echo(f'Downloading {parts} {files} to {book_path}')

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

            # Download the file
            self._download_book_file(account, file_data, file_path)

        # Finish download
        click.echo('')

    @staticmethod
    def table(metadata):
        """Video database table definition."""
        return Table(
            'books',
            metadata,
            Column('book_id', String, primary_key=True),
            Column('item_id', Integer, primary_key=True),
            Column('sku', String, primary_key=True),
            Column('title', String, nullable=False),
            Column('author', String, nullable=False),
            Column('drm', Boolean),
            Column('is_released', Boolean),
            Column('is_rental', Boolean),
            Column('purchase_date', DateTime, nullable=False),
            Column('release_date', Date, nullable=False),
            Column('runtime', Float, nullable=False),
            Column('url', URLType, nullable=False),
            Column('cover', URLType, nullable=False),
            Column('last_updated', DateTime, server_default=func.now(),
                   onupdate=func.now(), nullable=False),
        )

    @staticmethod
    def format_book_list(books, fmt='psql'):
        """Create a formatted list of books."""
        fields = ['ID', 'Title', 'Author', 'Runtime', 'Purchased']
        table_data = [[
            book.book_id,
            shorten(book.title, width=50),
            shorten(', '.join(book.author.split('|')), width=50),
            f'{book.runtime} hr',
            book.purchase_date.strftime('%d %b %y')
        ] for book in books]
        return tabulate(table_data, fields, tablefmt=fmt)

    @staticmethod
    def format_json_book_list(books):
        """Create a JSON-formatted list of books."""
        json_books = []
        for book in books:
            json_book = {}
            for column in book.__table__.columns:
                value = getattr(book, column.name)
                if isinstance(value, datetime) or isinstance(value, date):
                    json_book[column.name] = value.isoformat()
                    continue
                json_book[column.name] = value
            json_books.append(json_book)
        return json.dumps(json_books)

    def get_book(self, book_id):
        """Get book in database by ID."""
        book = self._find_book(book_id)
        if not book:
            self.manager.error(f'No book found for ID: {book_id}')
            return None
        return book

    @property
    def update(self):
        """Command to update the database with new books."""
        @click.command(help='Updates the the list of books.')
        @click.option('-l', '--list', 'list_', is_flag=True,
                      help='List any newly added minisodes')
        @self.auto_login_user(with_account=True)
        def fn(account, list_):
            """Updates the the list of books."""
            new_books = self._update_books(account.session)
            # Check for results
            if not new_books:
                self.manager.info('No new books found.')
                return
            # Print list of newly added books
            self.manager.success(f'Added {len(new_books)} new book(s)!')
            if list_:
                click.echo(self.format_book_list(new_books))
        return fn

    @property
    def download(self):
        """Command to download a given book."""
        @click.command(help='Download book(s) by ID(s).')
        @click.option('-y', '--yes', is_flag=True,
                      help='Download without confirmation.')
        @click.option('-d', '--dest', type=click.Path(exists=True),
                      help='Folder to download file(s) to.')
        @click.option('-f', '--file_type',
                      type=click.Choice([b.value for b in self.file_types]),
                      help='Set book file type to download.')
        @click.argument('book_ids', metavar='BOOK_ID', nargs=-1)
        @self.auto_login_user(with_account=True)
        def fn(account, book_ids, file_type, dest, yes):
            """Download book(s) by ID*s(."""
            for book_id in book_ids:
                book = self.get_book(book_id)
                if book:
                    self._download_book(account, book, dest, yes, file_type)
        return fn

    @property
    def show(self):
        """Command to display book details."""
        @click.command(help='Show book details by ID')
        @click.argument('book_id')
        def fn(book_id):
            """Show book details by ID."""
            book = self.get_book(book_id)
            if book:
                form = u'{0:>15}: {1}'
                book_data = '\n'.join([
                    form.format('Title', book.title),
                    form.format('Author(s)', ', '.join(book.author.split('|'))),
                    form.format('Runtime', f'{book.runtime} hours'),
                    form.format('Purchase Date', book.purchase_date.strftime('%d %B %Y')),
                    form.format('Released', 'Yes' if book.is_released else 'No'),
                    form.format('Rental', 'Yes' if book.is_rental else 'No'),
                    form.format('DRM', 'Yes' if book.drm else 'No'),
                    form.format('Link', book.url)
                ])
                click.echo(book_data)
        return fn

    @property
    def open(self):
        """Command to open book link in a browser."""
        @click.command(help='Open web page for book.')
        @click.argument('book_id')
        def fn(book_id):
            """Open web page for book."""
            book = self.get_book(book_id)
            if book:
                click.echo(f'Opening {book.url}')
                click.launch(book.url)
        return fn

    @property
    def list(self):
        """Command to display a list of books."""
        @click.command(help='Show all available books.')
        @click.option('-n', '--number', default=10, show_default=True,
                      help='Number of books to get.')
        @click.option('-r', '--refresh', is_flag=True,
                      help='Update list of books.')
        @click.option('-f', '--format', 'fmt', default='psql',
                      type=click.Choice(tabulate_formats), show_choices=False,
                      show_default=True, help='How to format the list.')
        @click.option('-s', '--search', type=click.STRING,
                      help='Search books by title and/or author.')
        @click.option('-j', '--json', 'as_json', is_flag=True,
                      help='Display book list as JSON.')
        @click.option('-a', '--asc', is_flag=True,
                      help='Sort in ascending order by purchase date.')
        def fn(number, refresh, fmt, search, as_json, asc):
            """Show all available books."""
            if refresh:
                account = self.login_user()
                self._update_books(account.session)
            # Set up query
            query = self.db.query(self.model)
            # Handle search query
            if search:
                query = query.filter(or_(
                    self.model.title.like(f'%{search}%'),
                    self.model.author.like(f'%{search}%')
                ))
            # Handler order by
            query = query.order_by(self.model.purchase_date.asc()
                                   if asc else self.model.purchase_date.desc())
            # Handle limit
            if number > 0:
                query = query.limit(number)
            # Run the query
            books = query.all()
            if not books:
                self.manager.warning('No books found.')
                return
            # Display the list
            book_list = self.format_json_book_list(books) \
                if as_json else self.format_book_list(books, fmt=fmt)
            click.echo(book_list)
        return fn
