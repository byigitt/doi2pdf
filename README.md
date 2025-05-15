# DOI to PDF Downloader

A Python application to download PDF versions of scientific articles using their Digital Object Identifier (DOI), name, or URL.

## Features (MVP)

- Download PDFs using DOI, paper name, or URL.
- Fetches metadata and attempts direct download via OpenAlex API.
- Falls back to Sci-Hub if direct download is unavailable, with two retrieval methods:
    - **Selenium (`undetected-chromedriver`)**: Default and more robust method, attempts to mimic a real browser session to bypass some anti-bot measures.
    - **Requests**: Legacy method using direct HTTP requests (less reliable for Sci-Hub).
- Configurable Sci-Hub domain via `SCI_HUB_URL` environment variable.
- Specify output directory for downloads.
- Sensible PDF filenames based on paper title.
- Optional: automatically open PDF after download.

## Installation

1.  Clone the repository (or download the script).
2.  Navigate to the `doi2pdf` directory.
3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
    *Note: The Selenium-based Sci-Hub retrieval method (`undetected-chromedriver`) typically requires Google Chrome to be installed on your system.*

## Usage

Replace `python main.py` with your actual script execution command (e.g., `python ./main.py` or just `main.py` if it's in your PATH and executable).

**Mandatory: You must provide one of the following identifiers:**
- `--doi <DOI_STRING>`
- `--name <PAPER_NAME>`
- `--url <PAPER_URL>`
- `--input-file <FILE_PATH>`

**General Options:**
- `-o <DIRECTORY_PATH>`, `--output <DIRECTORY_PATH>`: Specify the directory to save downloaded PDFs. Defaults to the current directory (`.`).
- `--open`: Automatically open the PDF after successful download using the system's default viewer.
- `--scihub-method {selenium,requests}`: Choose the method for Sci-Hub fallback. `selenium` is default and more robust. `requests` is less reliable.
- `--delay <SECONDS>`: Set a delay in seconds between downloads when using `--input-file` (batch mode). Default is 2 seconds.

**Examples:**

1.  **Download by DOI (simplest case, default output directory):**
    ```bash
    python main.py --doi "10.1234/example.doi"
    ```

2.  **Download by DOI, specify output directory, and automatically open:**
    ```bash
    python main.py --doi "10.1234/example.doi" -o "./my_papers" --open
    ```

3.  **Download by Paper Name:**
    ```bash
    python main.py --name "The Theory Of Everything" -o "./physics_papers/"
    ```

4.  **Download by URL (e.g., an article page, DOI link, or arXiv link):**
    ```bash
    python main.py --url "https://arxiv.org/abs/2303.08774" --output "./arxiv_downloads/"
    ```

5.  **Batch download from a file of DOIs:**
    Create a file (e.g., `my_dois.txt`) with one DOI per line:
    ```text
    10.1234/example1.doi
    10.5678/example2.doi
    10.9101/example3.doi
    ```
    Then run:
    ```bash
    python main.py --input-file ./my_dois.txt -o "./batch_downloads/" --delay 5
    ```

6.  **Using a specific Sci-Hub retrieval method (Selenium is default):**
    ```bash
    # Explicitly use Selenium (default, more robust)
    python main.py --doi "10.1109/ACCESS.2023.1234567" --scihub-method selenium

    # Use the less reliable requests-based method (not recommended for Sci-Hub)
    python main.py --doi "10.1109/ACCESS.2023.1234567" --scihub-method requests
    ```

7.  **Setting Sci-Hub URL via Environment Variable (then running a command):**

    *Powershell Example:*
    ```powershell
    $env:SCI_HUB_URL = "https://sci-hub.se" # Replace with a currently working Sci-Hub domain
    python main.py --doi "10.1234/example.doi"
    # To remove for the session (optional): Remove-Item Env:SCI_HUB_URL
    ```

    *Bash/Zsh Example:*
    ```bash
    export SCI_HUB_URL="https://sci-hub.se" # Replace with a currently working Sci-Hub domain
    python main.py --doi "10.1234/example.doi"
    # To remove for the session (optional): unset SCI_HUB_URL
    ```

**Combining options:**
Most options can be combined. For example, batch processing with auto-open, a specific output directory, and the Selenium method (which is default but can be explicit):
```bash
python main.py --input-file ./my_dois.txt -o "./papers_collection" --open --delay 2 --scihub-method selenium
```

## Important Notes on Sci-Hub Access

- **Volatility**: Sci-Hub domains change frequently. The script uses a default domain (`https://sci-hub.st`) but allows configuration via the `SCI_HUB_URL` environment variable. You may need to find and set a currently working domain.
- **Bot Detection & CAPTCHAs**: 
    - Sci-Hub sites often employ bot detection measures. 
    - The **Selenium-based method (`--scihub-method selenium`)**, which is the default, uses `undetected-chromedriver` to more closely mimic a real browser. This significantly improves the chances of bypassing basic anti-bot measures and JavaScript challenges compared to simple HTTP requests. However, it may still not overcome all obstacles, especially manual CAPTCHAs or very sophisticated detection.
    - The **requests-based method (`--scihub-method requests`)** uses direct HTTP requests and HTML parsing. It is far less likely to succeed if Sci-Hub employs even moderate anti-bot measures for a given DOI or from your IP address. *Currently, this method is not fully implemented for direct PDF fetching from Sci-Hub within the script and will likely fail to download the final PDF from Sci-Hub.*
- **Reliability**: Due to these factors, while the Selenium method enhances reliability, PDF retrieval via Sci-Hub can still be inconsistent. The script prioritizes direct downloads via OpenAlex when possible.

## Disclaimer

This tool is provided for educational or research convenience. Users are responsible for adhering to copyright laws and ethical guidelines in their respective jurisdictions. This tool does not host or distribute copyrighted material. 
