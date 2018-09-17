# EM Downpour Downloader #

Download [Downpour.com](#disclaimer) audiobook files.

## Installation

1. Download this repository:

    ```
    git clone https://github.com/ErinMorelli/em-downpour-downloader.git
    ```

2. Navigate into the folder created by step 1 and install the required python packages by running `pip`:

    ```
    pip install -r requirements.txt
    ```

3. Create the `downpour-downloader` folder in your home .config directory: 

    ```
    mkdir -p ~/.config/downpour-downloader
    ```

4. Open the `sample_config.yml` file with your favorite text editor and configure to suit your needs.

5. Save the file as `config.yml` and move it to the `downpour-downloader` folder you created in step 2:
    
    ```
    mv config.yml ~/.config/downpour-downloader/config.yml
    ```

6. Run the help command to make sure everything is working and to view the script's options:

    ```
    ./downpour.py --help
    ```


## Command-line Usage

EM Downpour Download has three commands you can use to view and download your audiobook files:

### Library

View a list of all available audiobooks associated with your Downpour account:

```
./downpour.py library
```


### Book Information

View additional information about a specific book by providing the book's `ID`, which is listed in the `library` command's output:

```
./downpour.py book abc1
```


### Download

Download an audiobook's files by providing the `ID`:

```
./downpour.py download abc1
```

To download multiple audiobooks at once, you can specify additional `ID` values separated by a space:

```
./downpour.py download abc1 def2 ghi3
```


## Disclaimer

[Downpour.com](http://www.downpour.com/) and Downpour are trademarks of Blackstone Audio, Inc., which is not affiliated with the maker of this product and does not endorse this product.

EM Downpour Downloader is free, open source software and is distributed under the [MIT license](https://opensource.org/licenses/MIT).
