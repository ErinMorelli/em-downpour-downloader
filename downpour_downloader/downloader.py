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
from stat import S_IRUSR

import click
from cryptography.fernet import Fernet

from sqlalchemy.orm import sessionmaker
from sqlalchemy import MetaData, create_engine
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.automap import automap_base

from .books import BooksContent
from .content import DownpourContent


class DownpourDownloader:
    """Core utilities and setup actions."""
    user_path = os.path.expanduser('~')
    # Manager configuration files storage location
    config_env_var = 'DP_DOWNLOAD_CONFIG_PATH'
    config_default = os.path.join(user_path, '.config', 'downpour-downloader')
    config_path = os.environ.get(config_env_var, None) or config_default
    # File to store password encryption key
    __key_file = os.path.join(config_path, '.secret_key')
    # URI for the local sqlite database file
    db_uri = f'sqlite:///{config_path}/content.db'
    # CLI context settings
    context_settings = {
        'help_option_names': ['-h', '--help']
    }
    #  List of all content type classes
    _types = [DownpourContent, BooksContent]

    def __init__(self):
        """Setup the sqlite database."""
        self._setup()
        # Encryption/decryption cipher handler
        self.__cipher = self.__get_cipher()
        # Setup the engine for the sqlite database
        self._engine = create_engine(self.db_uri)
        # Configure the SQLAlchemy metadata
        self._metadata = MetaData()
        self._metadata.bind = self._engine
        self._load_db()
        # Configure the auto-mapping base model
        self._base = automap_base(metadata=self._metadata)
        self._base.prepare()
        # Setup a session generator for database connections
        self._session = sessionmaker(bind=self._engine)

    def _setup(self):
        """Make sure files and folders exist."""
        if not os.path.isdir(self.config_path):
            os.makedirs(self.config_path)
        # Create a key file if one does not exist
        if not os.path.isfile(self.__key_file):
            with open(self.__key_file, 'wb') as f:
                f.write(Fernet.generate_key())
            # Make the file read-only
            os.chmod(self.__key_file, S_IRUSR)

    def __get_cipher(self):
        """Create a cipher manager from the stored key."""
        return Fernet(open(self.__key_file, 'rb').read())

    def encode(self, data):
        """Encode data with the cipher manager."""
        return self.__cipher.encrypt(data.encode('utf-8'))

    def decode(self, data):
        """Decode data with the cipher manager."""
        return self.__cipher.decrypt(data)

    def _load_db(self):
        """Dynamically loads database table schemas."""
        for type_ in self._types:
            try:
                type_.table(self._metadata)
            except InvalidRequestError:
                pass
        # Reflect metadata so auto-mapping works
        self._metadata.reflect(self._engine)
        # Make sure the tables exist
        self._metadata.create_all(self._engine)

    def get_session(self):
        """Create a new database session using the session maker."""
        return self._session()

    @property
    def models(self):
        """Object containing auto-mapped database model classes."""
        return self._base.classes

    @staticmethod
    def success(msg):
        """Print success message in green text."""
        click.secho(msg, fg='green')

    @staticmethod
    def info(msg):
        """Print info message in blue text."""
        click.secho(msg, fg='blue')

    @staticmethod
    def warning(msg):
        """Print warning message in yellow text."""
        click.secho(msg, fg='yellow')

    @staticmethod
    def error(msg):
        """Print error message in red text."""
        click.secho(f'[ERROR] {msg}', fg='red')

    @property
    def cli(self):
        """Base command group to load subcommands into."""
        @click.group(context_settings=self.context_settings)
        def fn():
            """Manage Patreon exclusive content."""
        # Dynamically load commands from content type classes
        for type_ in self._types:
            fn.add_command(type_(self).cli, type_.command)
        return fn
