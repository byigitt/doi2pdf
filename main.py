import argparse
from typing import Optional, Tuple, Any, Dict
import subprocess
import os
import platform
import bs4 # Renamed from beautifulsoup4 for consistency if used as bs4
import requests
import sys # For sys.exit
import time # Added for delay in batch processing
from datetime import datetime # For timestamped log file
from urllib.parse import urljoin
import re

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

class NotFoundError(Exception):
    """Custom exception for when a paper or PDF is not found."""
    pass

# Default Sci-Hub URL, can be overridden by environment variable
SCI_HUB_URL = os.getenv("SCI_HUB_URL", "https://sci-hub.mksa.top/") # Defaulting to .st for now based on user image

def sanitize_filename(title: str) -> str:
    """
    Sanitizes a string to be suitable for use as a filename.
    Replaces spaces with underscores and removes characters invalid for most filesystems.
    """
    if not title:
        title = "Unknown_Paper_Title"
    # Replace spaces with underscores first
    title = title.replace(' ', '_')
    # Define invalid characters for filenames (common across OS)
    # Windows is more restrictive, so we cater to that primarily.
    invalid_chars = '<>:\"/\\|?*'
    # Add control characters (0-31)
    invalid_chars += ''.join(chr(i) for i in range(32))

    for char in invalid_chars:
        title = title.replace(char, '')

    # Limit filename length (common limit is 255 bytes, be conservative)
    max_len = 200
    if len(title) > max_len:
        title = title[:max_len]

    # Ensure it doesn't end with a period or space (Windows issue)
    title = title.rstrip('. ')

    if not title: # If sanitization results in an empty string
        title = "Sanitized_Paper_Title"

    return title + ".pdf"

def get_paper_metadata(doi_in: Optional[str], name_in: Optional[str], url_in: Optional[str]) -> Tuple[Optional[str], str, Optional[str], Optional[str]]:
    """
    Fetches paper metadata from OpenAlex API.
    Returns (DOI, Title, PDF_URL, OpenAlexAPIURL)
    """
    print("Attempting to fetch metadata from OpenAlex...")
    base_api_url = "https://api.openalex.org/works"
    params = {}
    final_api_url_queried = None # To store the actual URL queried
    
    identifier_used = ""

    if doi_in:
        # Ensure DOI is just the identifier, not the full URL
        if doi_in.startswith("https://doi.org/"):
            doi_in = doi_in[len("https://doi.org/"):]
        elif doi_in.startswith("http://doi.org/"):
            doi_in = doi_in[len("http://doi.org/"):]
        
        api_url = f"{base_api_url}/https://doi.org/{doi_in}"
        identifier_used = f"DOI: {doi_in}"
    elif name_in:
        api_url = base_api_url # Search endpoint
        # Using filter for title search as per OpenAlex recommendations
        params = {"filter": f"title.search:{name_in}", "sort": "relevance_score:desc", "per-page": "1"}
        identifier_used = f"Name: {name_in}"
    elif url_in:
        # OpenAlex might be able to resolve some URLs directly if they are landing pages known to it
        # or if they are OpenAlex IDs themselves.
        if "doi.org" in url_in: 
            try:
                extracted_doi = url_in.split("doi.org/")[-1]
                api_url = f"{base_api_url}/https://doi.org/{extracted_doi}"
                identifier_used = f"URL (interpreted as DOI): {url_in}"
            except IndexError:
                 raise NotFoundError(f"Could not extract DOI from URL: {url_in}")
        elif "arxiv.org/abs/" in url_in or "arxiv.org/pdf/" in url_in:
            arxiv_id = url_in.split("arxiv.org/")[-1].replace("abs/", "").replace(".pdf", "")
            api_url = f"{base_api_url}/arxiv:{arxiv_id}"
            identifier_used = f"URL (interpreted as arXiv): {url_in}"
        else:
            # For generic URLs not clearly DOIs or OpenAlex IDs, a search might be better
            # For now, we'll try to see if OpenAlex can resolve it directly as a work ID
            # This might need to be a search like the 'name_in' case if it's just a generic URL
            print(f"Warning: Treating generic URL {url_in} as a potential OpenAlex work ID or expecting OpenAlex to resolve it. This may fail.")
            api_url = f"{base_api_url}/{url_in}" 
            identifier_used = f"URL: {url_in}"
    else:
        # This case should be caught by argparse, but as a safeguard:
        raise ValueError("No identifier (DOI, name, or URL) provided to get_paper_metadata.")

    final_api_url_queried = api_url # Store the API URL before adding params for GET request
    if params:
        # For logging/reporting purposes, show the full query if params were used (like in name search)
        final_api_url_queried_with_params = requests.Request('GET', api_url, params=params).prepare().url
        print(f"Querying OpenAlex for: {identifier_used} with URL: {final_api_url_queried_with_params}")
    else:
        print(f"Querying OpenAlex for: {identifier_used} with URL: {final_api_url_queried}")

    try:
        api_res = requests.get(api_url, params=params, timeout=15)
        api_res.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise NotFoundError(f"OpenAlex API request failed for {identifier_used}: {e}")

    metadata = api_res.json()

    if name_in and metadata.get("meta", {}).get("count", 0) == 0: # Check count for name search
        raise NotFoundError(f"No paper found on OpenAlex for name: {name_in}")
    if name_in and metadata.get("results"):
        metadata = metadata["results"][0]
    elif not metadata:
        raise NotFoundError(f"No metadata returned from OpenAlex for {identifier_used}")

    final_doi = metadata.get("doi")
    if final_doi and final_doi.startswith("https://doi.org/"):
        final_doi = final_doi[len("https://doi.org/"):]

    title = metadata.get("display_name", "Unknown Title")
    print(f"Found paper on OpenAlex: '{title}'")
    
    # PRD Order: oa_url, host_venue.url, primary_location.landing_page_url
    pdf_url = None
    open_access_info = metadata.get("open_access")
    if open_access_info:
        pdf_url = open_access_info.get("oa_url")
        if pdf_url:
            print(f"Found OpenAlex oa_url: {pdf_url}")

    if not pdf_url:
        host_venue = metadata.get("host_venue")
        if host_venue:
            # host_venue.url is often the journal page, not a direct PDF.
            # We should be cautious. Let's check if it's specifically marked as OA and is a PDF.
            # The PRD mentions host_venue.url. If it has 'pdf' in it and is_oa, might be worth a try.
            # For now, we prioritize more direct PDF links.
            # If we were to use it:
            # if host_venue.get("is_oa") and host_venue.get("url") and "pdf" in host_venue.get("url", "").lower():
            #    pdf_url = host_venue.get("url")
            #    print(f"Found potential PDF URL via host_venue.url: {pdf_url}")
            # For now, we are stricter for direct downloads, relying on more explicit PDF URLs.
            pass


    if not pdf_url:
        primary_location = metadata.get("primary_location")
        if primary_location and primary_location.get("is_oa"):
            # According to PRD, use primary_location.landing_page_url
            # Landing page URLs are often not direct PDFs.
            # However, best_oa_location is often more reliable for *direct* PDF.
            best_oa_loc = metadata.get("best_oa_location")
            if best_oa_loc and best_oa_loc.get("pdf_url"):
                 pdf_url = best_oa_loc.get("pdf_url")
                 print(f"Found PDF URL via best_oa_location.pdf_url: {pdf_url}")
            elif best_oa_loc and best_oa_loc.get("landing_page_url") and primary_location.get("is_oa"): # Fallback to landing page if pdf_url missing
                 # pdf_url = best_oa_loc.get("landing_page_url") # This is often an HTML page.
                 # print(f"Found landing page URL via best_oa_location.landing_page_url: {pdf_url}")
                 pass # Prefer not to use landing_page_url for direct download to avoid HTML pages.
            elif primary_location.get("landing_page_url"): # Last resort from PRD for primary_location
                 # pdf_url = primary_location.get("landing_page_url")
                 # print(f"Found landing page via primary_location.landing_page_url: {pdf_url}")
                 pass # Prefer not to use landing_page_url for direct download.
    
    if not pdf_url:
         print("No clearly direct Open Access PDF URL found in OpenAlex metadata (checked oa_url, best_oa_location.pdf_url).")

    # Special handling for arXiv URLs: ensure we have a .pdf link, not /abs/
    if pdf_url and "arxiv.org/abs/" in pdf_url:
        print(f"Original arXiv URL from OpenAlex: {pdf_url}")
        arxiv_id_match = re.search(r"arxiv.org/abs/([^/]+)", pdf_url)
        if arxiv_id_match:
            arxiv_id = arxiv_id_match.group(1)
            corrected_pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            print(f"Corrected arXiv URL to direct PDF: {corrected_pdf_url}")
            pdf_url = corrected_pdf_url
        else:
            print("Warning: Could not extract arXiv ID from abstract URL to form PDF URL.")

    return final_doi, title, pdf_url, final_api_url_queried


def get_html_content(url: str) -> bs4.BeautifulSoup:
    """Fetches HTML content from a URL and returns a BeautifulSoup object."""
    print(f"Fetching HTML from: {url}")
    try:
        # Using a more common User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        # Sci-Hub sometimes doesn't set encoding, or uses non-utf8.
        # BeautifulSoup can often handle this, but explicit utf-8 is a good default.
        response.encoding = response.apparent_encoding if response.apparent_encoding else 'utf-8'
        return bs4.BeautifulSoup(response.text, "html.parser")
    except requests.exceptions.RequestException as e:
        raise NotFoundError(f"Failed to fetch HTML from {url}: {e}")

def retrieve_scihub_pdf_link_selenium(doi: str, current_sci_hub_url: str, log_func) -> Optional[Tuple[str, Optional[list[dict]]]]:
    """Tries to retrieve the PDF link from Sci-Hub using Selenium and undetected-chromedriver."""
    if not doi:
        log_func("ERROR", "Selenium: DOI must be provided to retrieve from Sci-Hub.")
        raise ValueError("DOI must be provided for Sci-Hub retrieval.")

    scihub_page_url = f"{current_sci_hub_url.rstrip('/')}/{doi}"
    log_func("INFO", f"[Selenium] Attempting to retrieve PDF link from Sci-Hub page: {scihub_page_url}")

    options = uc.ChromeOptions()
    # options.add_argument('--headless') # Headless can sometimes be more detectable; test with and without
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    
    driver = None
    try:
        # Forcing a specific version might be needed if auto-download fails, but uc usually handles it.
        # driver = uc.Chrome(version_main=108) # Example if you have Chrome 108
        driver = uc.Chrome(options=options)
        driver.get(scihub_page_url)

        # Check for Sci-Hub's "document not found" message
        # time.sleep(0.5) # Replaced by WebDriverWait
        wait_for_text_timeout = 6 # seconds to wait for the specific text to appear
        try:
            # Check for either "not found" message or "request" message
            target_text_fragments = [
                "Unfortunately, Sci-Hub doesn't have the requested document:",
                "You can request this article"
            ]
            # Using XPath selector to find paragraphs containing target text
            xpath_selectors = [f"//p[contains(text(), \"{text}\")]" for text in target_text_fragments]
            xpath_condition = " | ".join(xpath_selectors) # Join XPath expressions with OR
            WebDriverWait(driver, wait_for_text_timeout).until(
                EC.presence_of_element_located((By.XPATH, xpath_condition))
            )
            # If the above line does not raise a TimeoutException, one of the elements is present
            log_func("WARNING", f"[Selenium] Sci-Hub page indicates document not found for DOI: {doi} (via specific text check)")
            raise NotFoundError(f"Sci-Hub does not have the document for DOI: {doi}")
        except TimeoutException: # Specifically catch TimeoutException if the text isn't found
            log_func("DEBUG", f"[Selenium] 'Not found' message not detected via specific text check within {wait_for_text_timeout}s. Proceeding to PDF check.")
        except WebDriverException as e:
            # Catch other potential WebDriver exceptions during this check
            log_func("DEBUG", f"[Selenium] WebDriverException during 'not found' message check: {e}. Proceeding to PDF check.")

        # Wait for potential iframe or embed to load
        # Sci-Hub pages can vary, look for common PDF containers
        # Prioritize #pdf iframe as it's most common
        wait_time = 20 # seconds
        pdf_src = None
        
        try:
            log_func("INFO", f"[Selenium] Looking for iframe with id='pdf' or embed with id='pdf'...")
            # Wait for either an iframe with id 'pdf' or an embed with id 'pdf'
            element = WebDriverWait(driver, wait_time).until(
                EC.presence_of_element_located((By.XPATH, "//iframe[@id='pdf'] | //embed[@id='pdf']"))
            )
            pdf_src = element.get_attribute("src")
            if pdf_src:
                log_func("INFO", f"[Selenium] Found PDF element (iframe/embed#pdf) with src: {pdf_src}")
            else:
                log_func("WARNING", "[Selenium] Found PDF element (iframe/embed#pdf) but it has no src.")

        except TimeoutException:
            log_func("WARNING", f"[Selenium] Timed out waiting for iframe/embed with id='pdf'. Trying other methods.")
            # Fallback: Look for any iframe whose src contains '.pdf'
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            if not iframes:
                log_func("INFO", "[Selenium] No iframes found on page.")
            for iframe in iframes:
                src_attr = iframe.get_attribute("src")
                if src_attr and ".pdf" in src_attr.lower():
                    log_func("INFO", f"[Selenium] Found iframe with .pdf in src: {src_attr}")
                    pdf_src = src_attr
                    break
                elif src_attr: # Log other iframe src for debugging
                    log_func("DEBUG", f"[Selenium] Found iframe with src: {src_attr} (does not contain .pdf)")

        if not pdf_src:
            log_func("ERROR", "[Selenium] Could not find PDF source via iframe/embed after all attempts.")
            raise NotFoundError("[Selenium] PDF iframe/embed not found on Sci-Hub page.")

        # Resolve URL (similar to requests version)
        if pdf_src.startswith("//"):
            pdf_src = "https:" + pdf_src
        elif not pdf_src.startswith("http"):
            # Use the current_sci_hub_url as base, as redirects might change driver.current_url
            pdf_src = urljoin(current_sci_hub_url, pdf_src)
        
        log_func("SUCCESS", f"[Selenium] Successfully retrieved PDF link: {pdf_src}")
        
        # Get cookies from the session
        selenium_cookies = None
        try:
            selenium_cookies = driver.get_cookies()
            if selenium_cookies:
                log_func("INFO", f"[Selenium] Extracted {len(selenium_cookies)} cookie(s) from the session.")
                # For debugging, you might want to log cookie names/domains, but be careful with sensitive values.
                # for cookie in selenium_cookies:
                #     log_func("DEBUG", f"[Selenium] Cookie: {cookie.get('name')} from {cookie.get('domain')}")
            else:
                log_func("INFO", "[Selenium] No cookies found in the current session.")
        except WebDriverException as e:
            log_func("WARNING", f"[Selenium] Could not extract cookies: {e}")
        
        return pdf_src, selenium_cookies

    except WebDriverException as e:
        log_func("CRITICAL_ERROR", f"[Selenium] WebDriverException occurred: {e}")
        raise NotFoundError(f"[Selenium] WebDriver error: {e}")
    except NotFoundError as e: # Catch our own NotFoundError from above
        raise e # Re-raise
    except Exception as e:
        log_func("CRITICAL_ERROR", f"[Selenium] An unexpected error occurred: {e}")
        raise NotFoundError(f"[Selenium] Unexpected error: {e}")
    finally:
        if driver:
            log_func("INFO", "[Selenium] Quitting WebDriver.")
            try:
                driver.quit() # Our explicit call to quit
                time.sleep(1) # Allow some time for the browser to close gracefully
                # Monkeypatch the instance's quit method to prevent __del__ from re-triggering issues
                driver.quit = lambda: None 
            except OSError as e:
                # Specifically catch OSError if quit() itself raises it.
                log_func("WARNING", f"[Selenium] OSError during explicit driver.quit(): {e}. This might be a cleanup issue with the driver.")
            except WebDriverException as e:
                log_func("WARNING", f"[Selenium] WebDriverException during explicit driver.quit(): {e}.")
            except Exception as e: # Catch any other unexpected error during quit
                log_func("WARNING", f"[Selenium] Unexpected error during explicit driver.quit(): {e}.")
            driver = None # Help with garbage collection
    return None, None # Ensure we always return a tuple


def download_pdf_content(pdf_url: str, selenium_cookies: Optional[list[dict]] = None) -> bytes:
    """Downloads raw PDF content from a URL, optionally using cookies from a Selenium session."""
    print(f"Attempting to download PDF from: {pdf_url}")
    
    # Strip the fragment from the URL
    pdf_url_to_download = pdf_url.split('#')[0]
    if pdf_url_to_download != pdf_url:
        print(f"Using URL with fragment stripped for download: {pdf_url_to_download}")
    else:
        # If no fragment, print original URL for clarity in logs if different from input
        print(f"Confirmed download URL (no fragment found or same): {pdf_url_to_download}")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36",
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8", 
            "Referer": SCI_HUB_URL 
        }
        
        cookie_jar = None
        if selenium_cookies:
            print("INFO: Using cookies from Selenium session for download.")
            cookie_jar = requests.cookies.RequestsCookieJar()
            for cookie in selenium_cookies:
                cookie_jar.set(cookie['name'], cookie['value'], domain=cookie['domain'], path=cookie['path'])
        
        response = requests.get(pdf_url_to_download, headers=headers, cookies=cookie_jar, stream=True, timeout=60)
        response.raise_for_status()
        
        content_type = response.headers.get("content-type", "").lower()
        # Peek at content if content-type is not application/pdf
        # Some servers might send PDFs with 'application/octet-stream'
        if "application/pdf" not in content_type and "octet-stream" not in content_type:
            # Check the first few bytes for PDF signature '%PDF-'
            # response.raw.peek() is good but requests.content will read it all anyway.
            # For stream=True, need to iterate or read.
            first_chunk = next(response.iter_content(chunk_size=1024, decode_unicode=False), b"")
            if not first_chunk.startswith(b"%PDF-"):
                # Log the content type and beginning of content for debugging
                print(f"Warning: Content-type is '{content_type}'. First 1KB not a PDF signature.")
                # print(f"Content preview: {first_chunk[:200]}") # Be careful printing binary
                raise NotFoundError(f"URL {pdf_url_to_download} did not return a PDF. Content-type: {content_type}. Try opening the URL in a browser.")
            pdf_bytes = first_chunk + response.content # Combine peeked chunk with the rest
        else:
            pdf_bytes = response.content

        if not pdf_bytes:
            raise NotFoundError(f"Downloaded PDF from {pdf_url_to_download} is empty.")
        return pdf_bytes
    except requests.exceptions.RequestException as e:
        raise NotFoundError(f"Failed to download PDF from {pdf_url_to_download}: {e}")


def open_file_externally(filepath: str):
    """Opens a file with the system's default application."""
    print(f"Attempting to open PDF: {filepath}")
    try:
        if not os.path.exists(filepath):
            print(f"Error: File not found at {filepath}, cannot open.")
            return

        if platform.system() == "Windows":
            os.startfile(os.path.abspath(filepath)) # Use abspath for startfile
        elif platform.system() == "Darwin":
            subprocess.call(["open", filepath])
        else: 
            subprocess.call(["xdg-open", filepath])
        print(f"Successfully launched application for: {filepath}")
    except Exception as e:
        print(f"Error opening PDF {filepath}: {e}. Please open it manually.")

def log_message(log_file_path: str, identifier: str, status: str, message: str):
    """Appends a message to the specified log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] Identifier: {identifier} | Status: {status} | Message: {message}\n"
    try:
        with open(log_file_path, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except IOError as e:
        print(f"Critical: Failed to write to log file {log_file_path}: {e}")

def update_simplified_summary(summary_file_path: str, identifier: str, status: str, message: Optional[str]):
    """Appends a simplified status update for an identifier to the summary file."""
    try:
        with open(summary_file_path, 'a', encoding='utf-8') as f_summary:
            f_summary.write(f"Identifier: {identifier} | Status: {status}\n")
            if status == "FAILURE" and message:
                # Ensure the message is a single line for this simplified summary
                # Or take the first line if it's multi-line from the detailed log
                first_line_of_message = message.split('|')[0].strip() if message else "No specific reason logged."
                f_summary.write(f"  -> Reason: {first_line_of_message}\n")
            f_summary.write("---\n") # Add a separator for readability
    except IOError as e:
        print(f"ERROR: Failed to update simplified summary report {summary_file_path}: {e}")

def doi_to_pdf_downloader(
    identifier_doi: Optional[str] = None,
    identifier_name: Optional[str] = None,
    identifier_url: Optional[str] = None,
    output_dir: str = ".",
    auto_open_pdf: bool = False,
    is_batch_mode: bool = False,
    log_file_path: Optional[str] = None,
    scihub_retrieval_method: str = "selenium",
    summary_file_path: Optional[str] = None
) -> Dict[str, Any]:
    """Main logic to download PDF given an identifier. Returns a dictionary with summary info."""
    
    input_identifier_str = identifier_doi or identifier_name or identifier_url or "Unknown Identifier"
    summary_data: Dict[str, Any] = {
        "identifier_input": input_identifier_str,
        "resolved_doi": None,
        "paper_title": "Unknown_Paper",
        "status": "INITIATED",
        "message": "",
        "openalex_api_url_queried": None,
        "direct_oa_pdf_url_tried": None,
        "scihub_page_url_tried": None,
        "final_pdf_source_url": None,
        "local_filepath": None
    }

    def _log(status: str, msg: str):
        print(f"[{status}] {msg}")
        if log_file_path:
            log_message(log_file_path, input_identifier_str, status, msg)
        # Update summary data based on critical log messages
        if status in ["SUCCESS", "FAILURE", "ERROR", "CRITICAL_ERROR", "WARNING"]:
            if summary_data["status"] != "SUCCESS": # Don't overwrite SUCCESS with a later WARNING
                 summary_data["status"] = status 
            # Concatenate messages for summary if multiple errors/warnings occur
            summary_data["message"] = (summary_data["message"] + " | " + msg) if summary_data["message"] else msg

    final_doi = None
    paper_title = "Unknown_Paper" # Default title
    pdf_content = None
    final_api_url_queried = None
    direct_pdf_url_from_oa = None
    scihub_pdf_url_retrieved = None
    actual_download_source_url = None

    # Stage 1: Get metadata and potentially a direct PDF URL from OpenAlex
    try:
        _log("INFO", f"Starting process for: {input_identifier_str}")
        final_doi, paper_title, direct_pdf_url_from_oa, final_api_url_queried = get_paper_metadata(identifier_doi, identifier_name, identifier_url)
        summary_data["resolved_doi"] = final_doi
        summary_data["paper_title"] = paper_title
        summary_data["openalex_api_url_queried"] = final_api_url_queried
        summary_data["direct_oa_pdf_url_tried"] = direct_pdf_url_from_oa
        
        if direct_pdf_url_from_oa:
            _log("INFO", f"Attempting direct download from OpenAlex URL: {direct_pdf_url_from_oa}")
            try:
                pdf_content = download_pdf_content(direct_pdf_url_from_oa)
                _log("SUCCESS", "Successfully downloaded PDF from OpenAlex source.")
                summary_data["status"] = "SUCCESS"
                actual_download_source_url = direct_pdf_url_from_oa
            except NotFoundError as e:
                _log("WARNING", f"Direct download from OpenAlex URL failed: {e}. Will try Sci-Hub if DOI is available.")
                summary_data["status"] = "FAILURE_DIRECT_DOWNLOAD"
        else:
            _log("INFO", "No direct PDF URL found via OpenAlex.")
            summary_data["status"] = "INFO_NO_DIRECT_OA_URL" # Intermediate status

    except NotFoundError as e:
        _log("ERROR", f"Error fetching metadata from OpenAlex: {e}.")
        summary_data["status"] = "FAILURE_OPENALEX_METADATA"
        if identifier_doi:
            final_doi = identifier_doi # Use original DOI for Sci-Hub attempt
            summary_data["resolved_doi"] = final_doi
            paper_title = identifier_doi.replace("/", "_").replace(".", "_") 
            summary_data["paper_title"] = paper_title
            _log("INFO", "Will attempt Sci-Hub using the provided DOI.")
        else:
            _log("ERROR", "Could not obtain DOI from OpenAlex to attempt Sci-Hub download.")
            if not is_batch_mode: sys.exit(1)
            return summary_data # Return current summary
    except ValueError as e: # From get_paper_metadata if no identifier
        _log("ERROR", f"Input error for metadata retrieval: {e}")
        summary_data["status"] = "FAILURE_INPUT_ERROR"
        if not is_batch_mode: sys.exit(1)
        return summary_data # Return current summary

    # Stage 2: Fallback to Sci-Hub
    if not pdf_content and final_doi:
        scihub_page_to_try = f"{SCI_HUB_URL.rstrip('/')}/{final_doi}"
        summary_data["scihub_page_url_tried"] = scihub_page_to_try
        _log("INFO", f"Attempting Sci-Hub fallback for DOI: {final_doi} using {scihub_retrieval_method} method.")
        try:
            if scihub_retrieval_method == "selenium":
                scihub_pdf_url_retrieved, selenium_cookies = retrieve_scihub_pdf_link_selenium(final_doi, SCI_HUB_URL, _log)
            else:
                 _log("WARNING", "Requests-based Sci-Hub retrieval selected but not fully implemented for PDF download.")
                 raise NotFoundError("Requests-based Sci-Hub method not fully implemented for PDF download.")

            if scihub_pdf_url_retrieved:
                _log("INFO", f"Found Sci-Hub PDF URL: {scihub_pdf_url_retrieved}")
                pdf_content = download_pdf_content(scihub_pdf_url_retrieved, selenium_cookies if scihub_retrieval_method == "selenium" else None)
                _log("SUCCESS", "Successfully downloaded PDF from Sci-Hub.")
                summary_data["status"] = "SUCCESS"
                actual_download_source_url = scihub_pdf_url_retrieved
            else:
                _log("ERROR", "Sci-Hub retrieval function did not return a PDF URL.")
                if summary_data["status"] != "FAILURE_SCIHUB_NOT_FOUND":
                    summary_data["status"] = "FAILURE_SCIHUB_NO_URL"
        except NotFoundError as e:
            if "Sci-Hub does not have the document" in str(e):
                summary_data["status"] = "FAILURE_SCIHUB_NOT_FOUND"
            else:
                summary_data["status"] = "FAILURE_SCIHUB_DOWNLOAD_ERROR"
            _log("ERROR", f"Sci-Hub download failed: {e}")
        except ValueError as e: 
            _log("ERROR", f"Error with Sci-Hub retrieval input (e.g. invalid DOI): {e}")
            summary_data["status"] = "FAILURE_SCIHUB_INPUT_ERROR"
        except Exception as e:
            _log("CRITICAL_ERROR", f"An unexpected error occurred during Sci-Hub Selenium retrieval: {e}")
            summary_data["status"] = "FAILURE_SCIHUB_UNEXPECTED_ERROR"
    elif not pdf_content and not final_doi:
        _log("WARNING", "No PDF downloaded from OpenAlex and no DOI available for Sci-Hub attempt.")
        if summary_data["status"] not in ["FAILURE_OPENALEX_METADATA", "FAILURE_INPUT_ERROR"]:
             summary_data["status"] = "FAILURE_NO_DOI_FOR_SCIHUB"

    summary_data["final_pdf_source_url"] = actual_download_source_url

    # Stage 3: Save the PDF
    if pdf_content:
        filename = sanitize_filename(paper_title)
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                _log("INFO", f"Created output directory: {output_dir}")
            except OSError as e:
                _log("ERROR", f"Error creating output directory {output_dir}: {e}. Saving to current directory instead.")
                output_dir = "."
        
        output_filepath = os.path.join(output_dir, filename)
        summary_data["local_filepath"] = output_filepath

        try:
            with open(output_filepath, "wb") as f:
                f.write(pdf_content)
            _log("SUCCESS", f"PDF successfully saved to: {output_filepath}")
            summary_data["status"] = "SUCCESS"
            summary_data["message"] = f"PDF successfully saved to: {output_filepath}"

            if auto_open_pdf:
                open_file_externally(output_filepath)
        except IOError as e:
            _log("ERROR", f"Error saving PDF to {output_filepath}: {e}")
            summary_data["status"] = "FAILURE_SAVE_PDF"
            summary_data["local_filepath"] = None
            if not is_batch_mode: sys.exit(1)
    else:
        if summary_data["status"] not in ["SUCCESS", "FAILURE_OPENALEX_METADATA", "FAILURE_INPUT_ERROR", "FAILURE_SCIHUB_NOT_FOUND", "FAILURE_NO_DOI_FOR_SCIHUB"]:
            summary_data["status"] = "FAILURE_NO_PDF_CONTENT"
        _log("FAILURE", f"Ultimately failed to download PDF for identifier: {input_identifier_str}.")
        if not is_batch_mode: sys.exit(1)

    # Update simplified summary file incrementally
    if summary_file_path:
        final_status_str = "SUCCESS" if summary_data.get("status") == "SUCCESS" else "FAILURE"
        # Use the potentially multi-part message from summary_data for the reason
        reason_message = summary_data.get("message") if final_status_str == "FAILURE" else None
        update_simplified_summary(summary_file_path, input_identifier_str, final_status_str, reason_message)

    return summary_data

def main():
    parser = argparse.ArgumentParser(
        description="Downloads PDF versions of scientific articles using DOI, name, or URL. Prioritizes OpenAlex for direct OA links, then falls back to Sci-Hub.",
        epilog=f"Example: python {os.path.basename(__file__)} --doi \"10.1234/example.doi\" -o \"./downloads\" --open\n"
                 f"Batch example: python {os.path.basename(__file__)} --input-file my_dois.txt -o \"./papers\""
    )

    # Group for individual identifiers
    identifier_group = parser.add_argument_group('Individual Identifier Options')
    mutually_exclusive_group = identifier_group.add_mutually_exclusive_group()
    mutually_exclusive_group.add_argument(
        "--doi", type=str, help="Digital Object Identifier (DOI) of the research paper."
    )
    mutually_exclusive_group.add_argument(
        "--name", type=str, help="Name/title of the research paper (will be searched on OpenAlex)."
    )
    mutually_exclusive_group.add_argument(
        "--url", type=str, help="URL of the research paper (e.g., article page, DOI link, or OpenAlex work URL)."
    )

    # Option for batch processing
    parser.add_argument(
        "--input-file", type=str, help="Path to a text file containing DOIs, one per line, for batch processing."
    )

    parser.add_argument(
        "-o", "--output", type=str, default=".",
        help="Output directory for the downloaded PDF(s). Defaults to the current directory."
    )
    parser.add_argument(
        "--open", action="store_true",
        help="Automatically open each PDF file after downloading using the system's default viewer."
    )
    parser.add_argument(
        "--delay", type=int, default=0, help="Delay in seconds between downloads in batch mode. Default: 2 seconds."
    )
    
    # Add argument for Sci-Hub method
    parser.add_argument(
        "--scihub-method", 
        type=str, 
        default="selenium", 
        choices=["selenium", "requests"], 
        help="Method to use for Sci-Hub retrieval. 'selenium' (default) uses a browser, 'requests' uses direct HTTP (less reliable)."
    )

    parser.epilog += f"\n\nNote: The Sci-Hub domain can be configured via the SCI_HUB_URL environment variable. Current default: {SCI_HUB_URL}"

    args = parser.parse_args()

    # Validation: Ensure either an individual identifier or an input file is provided, but not both simultaneously with individual id.
    if args.input_file and (args.doi or args.name or args.url):
        parser.error("Cannot use --input-file simultaneously with --doi, --name, or --url. Provide one mode of operation.")
    if not args.input_file and not (args.doi or args.name or args.url):
        parser.error("Either an individual identifier (--doi, --name, --url) or an --input-file must be provided.")

    # Prepare log file path (placed in the output directory)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"download_log_{timestamp_str}.txt"
    # Ensure output directory exists before creating log file path within it
    if not os.path.exists(args.output):
        try:
            os.makedirs(args.output, exist_ok=True)
            print(f"INFO: Created output directory for logs and PDFs: {args.output}")
        except OSError as e:
            print(f"ERROR: Could not create output directory {args.output}: {e}. Log and PDFs will be in current directory.")
            args.output = "." # Fallback
    
    master_log_file_path = os.path.join(args.output, log_filename)
    print(f"INFO: Operation log will be saved to: {master_log_file_path}")

    # Prepare summary file path and write header
    summary_filename = f"download_summary_{timestamp_str}.txt"
    summary_file_path = os.path.join(args.output, summary_filename)
    try:
        with open(summary_file_path, 'w', encoding='utf-8') as f_summary: # 'w' to create/overwrite
            f_summary.write(f"--- Download Summary ({timestamp_str}) ---\n\n")
        print(f"INFO: Initialized summary report at {summary_file_path}")
    except IOError as e:
        print(f"ERROR: Failed to initialize summary report {summary_file_path}: {e}. Summary will not be written.")
        summary_file_path = None # Disable summary writing if header fails
    
    processing_summaries: list[Dict[str, Any]] = [] # Still used to collect data, though not for final summary write

    if args.input_file:
        if not os.path.exists(args.input_file):
            err_msg = f"Input file not found: {args.input_file}"
            print(f"ERROR: {err_msg}")
            log_message(master_log_file_path, "BATCH_SETUP", "ERROR", err_msg)
            sys.exit(1)
        
        log_message(master_log_file_path, "BATCH_MODE", "INFO", f"Starting batch processing from file: {args.input_file}")
        print(f"Starting batch processing from file: {args.input_file}")
        with open(args.input_file, 'r', encoding='utf-8') as f:
            identifiers_to_process = [line.strip() for line in f if line.strip()]
        
        total_items = len(identifiers_to_process)
        log_message(master_log_file_path, "BATCH_MODE", "INFO", f"Found {total_items} identifiers to process.")
        print(f"Found {total_items} identifiers to process.")

        for i, identifier_str in enumerate(identifiers_to_process):
            print(f"\nProcessing item {i+1}/{total_items}: {identifier_str}") 
            log_message(master_log_file_path, identifier_str, "INFO", f"Starting processing for item {i+1}/{total_items}")
            try:
                summary = doi_to_pdf_downloader(
                    identifier_doi=identifier_str, 
                    identifier_name=None, 
                    identifier_url=None,  
                    output_dir=args.output,
                    auto_open_pdf=args.open,
                    is_batch_mode=True,
                    log_file_path=master_log_file_path,
                    scihub_retrieval_method=args.scihub_method,
                    summary_file_path=summary_file_path
                )
                processing_summaries.append(summary)
            except Exception as e:
                err_msg = f"Critical unexpected error processing identifier {identifier_str}: {e}"
                print(f"ERROR: {err_msg}")
                log_message(master_log_file_path, identifier_str, "CRITICAL_ERROR", err_msg)
            
            if i < total_items - 1 and args.delay > 0:
                wait_msg = f"Waiting for {args.delay} seconds before next item..."
                print(wait_msg)
                time.sleep(args.delay)
        
        final_batch_msg = "Batch processing complete."
        print(f"\n{final_batch_msg}")
        log_message(master_log_file_path, "BATCH_MODE", "INFO", final_batch_msg)

    else: # Single identifier mode
        log_message(master_log_file_path, (args.doi or args.name or args.url), "INFO", "Starting single item processing.")
        summary = doi_to_pdf_downloader(
            identifier_doi=args.doi,
            identifier_name=args.name,
            identifier_url=args.url,
            output_dir=args.output,
            auto_open_pdf=args.open,
            is_batch_mode=False,
            log_file_path=master_log_file_path,
            scihub_retrieval_method=args.scihub_method,
            summary_file_path=summary_file_path
        )
        processing_summaries.append(summary)
        final_single_msg = "Single item processing complete."
        print(final_single_msg)
        log_message(master_log_file_path, (args.doi or args.name or args.url), "INFO", final_single_msg)
    
    # The final summary writing loop is removed, as it is now incremental.
    # A final message indicating completion and where logs/summary are saved.
    print(f"\nINFO: All processing finished. Main log: {master_log_file_path}")
    if summary_file_path:
        print(f"INFO: Incremental summary report: {summary_file_path}")
    else:
        print("WARNING: Summary report writing was disabled due to an earlier error.")

if __name__ == "__main__":
    main()