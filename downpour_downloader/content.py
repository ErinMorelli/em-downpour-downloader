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
import enum
import time

import click
from bs4 import BeautifulSoup
from requests import Session, RequestException

from yaspin import yaspin
from yaspin.spinners import Spinners

from sqlalchemy_utils import EmailType
from sqlalchemy.dialects.sqlite import BLOB
from sqlalchemy.orm.exc import MultipleResultsFound, NoResultFound
from sqlalchemy import Table, Column, String, DateTime, PickleType, Enum, func


class BookFileType(enum.Enum):
    """Valid file types for downloading from Downpour."""
    M4B = 'm4b'
    ZIP = 'zip'


class DownpourContent:
    """Base content class that also provides account access."""
    command = 'account'
    model_name = command
    # URLs for making requests
    base_url = 'https://www.downpour.com'
    library_url = f'{base_url}/my-library'
    cart_url = f'{base_url}/blackstone_custom/ajax/getCartCount'
    # Common headers for HTTP requests
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:89.0) '
                      'Gecko/20100101 Firefox/89.0'
    }
    # Common parser for BS4
    parser = 'html.parser'
    # Valid book filetypes
    file_types = BookFileType
    # Default download path
    download_dir = os.path.join(os.path.expanduser('~'), 'Audiobooks')
    # Set CLI details for account management
    command_help = 'Manage your Downpour account.'
    commands = ['login', 'update', 'show']

    def __init__(self, manager):
        """Setup details for content class."""
        self.manager = manager
        self.db = self.manager.get_session()
        self.model = self.manager.models.get(self.model_name)

    def auto_login_user(self, with_account=False):
        """Decorator to automatically log user in for CLI actions."""
        def inner(fn):
            def wrapper(*args, **kwargs):
                with yaspin(spinner=Spinners.line):
                    account = self.login_user()
                if not account:
                    return
                if with_account:
                    kwargs['account'] = account
                fn(*args, **kwargs)
            return wrapper
        return inner

    def session(self, session_cookies):
        """Create a new API session with the correct cookies and headers."""
        session = Session()
        # Add cookies and headers to session
        session.cookies.update(session_cookies)
        session.headers = self.headers
        # Return new session object
        return session

    def _get_account(self):
        """Locate an account in the database."""
        model = self.manager.models.get('account')
        try:
            account = self.db.query(model).one()
        except NoResultFound:
            # Ask user to login
            self.manager.warning('Please login to your account first.')
            return None
        except MultipleResultsFound:
            # List all available accounts
            click.echo('Multiple accounts found:')
            all_accounts = self.db.query(model).all()
            for idx, acct in enumerate(all_accounts):
                click.echo(f' [{idx}] {acct.email}')
            # Prompt user to select an account
            user_idx = click.prompt(
                '\nEnter account number',
                type=click.Choice(range(len(all_accounts))),
                show_choices=False,
                default='0'
            )
            # Use the selected account
            account = all_accounts[int(user_idx)]
        # Returns the account or none
        return account

    def _check_session(self, account):
        """Check if the user's session ID is still valid."""
        res = self.session(account.session).get(self.cart_url)
        try:
            res.raise_for_status()
            cart_data = res.json()['data']['count']
        except (RequestException, KeyError):
            return False

        # Check the results
        return cart_data is not None

    def login_user(self):
        """Login with Patreon credentials."""
        account = self._get_account()
        if not account:
            return None

        # If the session is valid, skip login request
        if self._check_session(account):
            return account

        # Make the login request
        session = self._make_login_request(
            account.email,
            self.manager.decode(account.password)
        )
        if not session:
            return None

        # Store new session
        account.session = session
        self.db.commit()

        # Return the logged in account
        return account

    def _make_login_request(self, email, password):
        """Make a login request with the given credentials"""
        session = Session()

        # Load the home page
        home = session.get(self.base_url, headers=self.headers)
        home_soup = BeautifulSoup(home.text, self.parser)

        # Look for login URL
        login_link = home_soup.find('a', string='Sign In')
        if not login_link:
            self.manager.error('Unable to login: cannot find sign in link')
            return None

        # Navigate to login page
        post = session.get(login_link['href'], headers=self.headers)
        post_soup = BeautifulSoup(post.text, 'html.parser')

        # Look for post URL
        login_form = post_soup.find('form', id='login-form')
        if not login_form:
            self.manager.error('Unable to login: cannot find login form')
            return None

        # Look for form key
        form_key_input = login_form.find('input', attrs={'name': 'form_key'})
        if not form_key_input:
            self.manager.error('Unable to login: cannot find login form key')
            return None

        # Login to Downpour
        try:
            login = session.post(
                login_form['action'],
                headers=self.headers,
                data={
                    'form_key': form_key_input['value'],
                    'login[username]': email,
                    'login[password]': password,
                    'send': ''
                }
            )
            login.raise_for_status()
            login_soup = BeautifulSoup(login.text, self.parser)
        except RequestException as ex:
            self.manager.error(f'Unable to login: {str(ex)}')
            return None

        # Check for success
        if not login_soup.find('a', string='Signout'):
            self.manager.error('Unable to login: invalid login or password')
            return None

        # Return user cookies
        return login.cookies

    @staticmethod
    def table(metadata):
        """Account database table definition."""
        return Table(
            'account',
            metadata,
            Column('email', EmailType, primary_key=True, unique=True),
            Column('password', BLOB, nullable=False),
            Column('file_type', Enum(BookFileType), default=BookFileType.M4B),
            Column('folder_template', String, default='{author}/{title}'),
            Column('download_dir', String, nullable=True),
            Column('session', PickleType, nullable=True),
            Column('last_updated', DateTime, server_default=func.now(),
                   onupdate=func.now(), nullable=False)
        )

    @property
    def login(self):
        """Command to login user."""
        @click.command(help='Login with your Downpour credentials.')
        @click.option('-e', '--email', prompt='Email')
        @click.password_option('-p', '--password')
        def fn(email, password):
            """Login with your Downpour credentials."""
            account = self.db.query(self.model) \
                .filter_by(email=email) \
                .first()
            # Add account if it is not found
            if not account:
                # Attempt to login
                session = self._make_login_request(email, password)
                # Save the account if successful
                if session:
                    account = self.model(
                        email=email,
                        password=self.manager.encode(password),
                        download_dir=self.download_dir,
                        session=session
                    )
                    self.db.add(account)
                    self.db.commit()
                    self.manager.success('Successfully logged in!')
                return
            # Attempt to login
            session = self._make_login_request(email, password)
            # If the account was found, confirm password change
            if session and click.confirm('Confirm password change'):
                account.password = self.manager.encode(password)
                account.session = session
                self.db.commit()
                self.manager.success('Successfully updated password!')
        return fn

    @property
    def update(self):
        """Command to update account info."""
        @click.command(help='Update account information.',
                       no_args_is_help=True)
        @click.option('--file_type', help='Set book file type to download.',
                      type=click.Choice([b.value for b in BookFileType]))
        @click.option('--folder_template', type=click.STRING,
                      help='Set template for folder structure when downloading '
                           'books. Available variables: author, title, book_id')
        @click.option('--download_dir', type=click.Path(exists=True),
                      help='Set path where files will be downloaded.')
        @self.auto_login_user(with_account=True)
        def fn(account, file_type, folder_template, download_dir):
            """Update account information."""
            changed = []
            # Handle file type change
            if file_type:
                account.file_type = file_type
                changed.append(('file type', file_type))
            # Handle folder template change
            if folder_template:
                account.folder_template = folder_template
                changed.append(('folder template', folder_template))
            # Handle download dir change
            if download_dir:
                account.download_dir = download_dir
                changed.append(('download path', download_dir))
            # Commit any changes and print details
            if changed:
                self.db.commit()
                for setting_name, new_value in changed:
                    self.manager.success(f'Set {setting_name} to: {new_value}')
        return fn

    @property
    def show(self):
        """Command to show account info."""
        @click.command(help='Display account information.')
        @self.auto_login_user(with_account=True)
        def fn(account):
            """Display account information."""
            form = u'{0:>15}: {1}'
            account_data = '\n'.join([
                form.format('Email', account.email),
                form.format('Password', '*********** [hidden for security]'),
                form.format('Download Path', account.download_dir),
                form.format('Folder Template', account.folder_template),
                form.format('File Type', account.file_type.value)
            ])
            click.echo(account_data)
        return fn

    @property
    def cli(self):
        """Command grouping for content actions."""
        @click.group()
        def fn():
            """Base group function for creating the CLI."""
        # Set the description
        fn.help = self.command_help
        # Add all account commands
        for cmd in self.commands:
            fn.add_command(getattr(self, cmd), cmd)
        return fn
