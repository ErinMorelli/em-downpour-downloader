# EM Downpour Downloader #

Download [Downpour.com](#disclaimer) audiobook files.

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=ErinMorelli_em-downpour-downloader&metric=alert_status)](https://sonarcloud.io/dashboard?id=ErinMorelli_em-downpour-downloader)

---

## Installation

1. Download this repository:

    ```
    $ git clone https://github.com/ErinMorelli/em-downpour-downloader.git
    ```

2. Navigate into the folder created by step 1 and install the required python packages by running:

    ```
    $ python setup.py install
    ```

3. Run the help command to make sure everything is working and to view the script's options:

    ```
    $ downpour --help
    ```

4. Log in to your Downpour account to start using the app:

    ```
    $ downpour account login
    ```

5. Do an initial load of all of your books into the app library by running:

   ```
   $ downpour books update
   ```


## Command-line Usage

EM Downpour Download has many commands you can use to view and download your audiobook files:

### Library

View a list of all available audiobooks associated with your Downpour account:

```
$ downpour books list
```


### Book Information

View additional information about a specific book by providing the book's `ID`, which is listed in the `library` command's output:

```
$ downpour books show abc1
```


### Download

Download an audiobook's files by providing the `ID`:

```
$ downpour books download abc1
```

To download multiple audiobooks at once, you can specify additional `ID` values separated by a space:

```
$ downpour books download abc1 def2 ghi3
```


## Disclaimer

[Downpour.com](http://www.downpour.com/) and Downpour are trademarks of Blackstone Audio, Inc., which is not affiliated with the maker of this product and does not endorse this product.

EM Downpour Downloader is free, open source software and is distributed under the [MIT license](https://opensource.org/licenses/MIT).
