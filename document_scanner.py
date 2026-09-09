import csv
import logging
import os
import time
from pathlib import Path
 
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
 
 
# ============================================================
# CONFIGURATION
# ============================================================
 
UPTICK_URL = "https://stokes.onuptick.com"
 
INPUT_FILE = "properties.csv"
OUTPUT_FILE = "test_Output.csv"
 
FOLDER_CLICK_SETTLE_MS = 1000
PAGE_LOAD_SETTLE_MS = 3500
NAV_TIMEOUT_MS = 60000
 
 
# ============================================================
# LOGGING
# ============================================================
 
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
 
log = logging.getLogger("uptick_scanner")
 
 
def login(page) -> None:
    """Sign in using environment credentials, with manual/MFA fallback."""
    email = os.getenv("UPTICK_EMAIL", "").strip()
    password = os.getenv("UPTICK_PASSWORD", "")
    if not email or not password:
        input("Log in to Uptick manually, then press ENTER here...")
        return
    try:
        email_input = page.locator(
            "input[type='email'], input[name='email'], input[placeholder='Email']"
        ).first
        password_input = page.locator(
            "input[type='password'], input[name='password'], input[placeholder='Password']"
        ).first
        email_input.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
        email_input.fill(email)
        password_input.fill(password)
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.wait_for_timeout(2000)
        if page.locator("input[type='password']").count() and page.locator("input[type='password']").first.is_visible():
            input("Complete MFA/verification in the browser, then press ENTER here...")
    except Exception as exc:
        log.warning("Automatic login failed: %s", exc)
        input("Log in to Uptick manually, then press ENTER here...")
 
 
# ============================================================
# READ PROPERTY IDS
# ============================================================
 
def get_property_ids() -> list[str]:
 
    log.info("\nReading property CSV...")
 
    df = pd.read_csv(INPUT_FILE)
 
    log.info("CSV columns: %s", df.columns.tolist())
 
    property_ids = []
 
    for value in df.iloc[:, 0]:
 
        if pd.isna(value):
            continue
 
        property_id = str(value).strip()
 
        if property_id.endswith(".0"):
            property_id = property_id[:-2]
 
        if property_id:
            property_ids.append(property_id)
 
    return property_ids
 
 
# ============================================================
# CSV RESULT WRITER
# ============================================================
 
class ResultsWriter:
 
    def __init__(self, path: str):
 
        self.path = path
 
        self._file = open(
            path,
            "w",
            newline="",
            encoding="utf-8-sig"
        )
 
        self._writer = csv.writer(self._file)
 
        self._writer.writerow(
            [
                "Property ID",
                "Property Name",
                "Document Name"
            ]
        )
 
        self._file.flush()
 
    def add(
        self,
        property_id: str,
        property_name: str,
        documents: list[str]
    ):
 
        if documents:
 
            # Put ALL documents into ONE Excel/CSV cell
            document_text = ", ".join(documents)
 
        else:
 
            document_text = "No Document"
 
        self._writer.writerow(
            [
                property_id,
                property_name,
                document_text
            ]
        )
 
        self._file.flush()
 
    def close(self):
 
        self._file.close()
 
 
# ============================================================
# GET PROPERTY NAME
# ============================================================
 
def get_property_name(page) -> str:
 
    try:
 
        heading = page.locator(
            "div.property_body h3"
        ).first
 
        if heading.is_visible():
 
            name = heading.inner_text().strip()
 
            name = " ".join(name.split())
 
            return name
 
    except Exception as e:
 
        log.warning(
            "Could not get property name: %s",
            e
        )
 
    return ""
 
 
# ============================================================
# FOLDER DETECTION
# ============================================================
 
def get_folder_elements(page):
 
    """
    Find possible folder elements.
 
    We intentionally do NOT depend on one specific
    folder icon class because Uptick's HTML can vary.
    """
 
    selectors = [
 
        # Existing Uptick structure
        "svg.fa-folder",
        "svg.fa-folder-open",
 
        # Generic folder classes
        "[class*='folder']",
 
        # Elements having folder title
        "span[title]",
 
    ]
 
    elements = []
 
    seen = set()
 
    for selector in selectors:
 
        try:
 
            locator = page.locator(selector)
 
            count = locator.count()
 
            for i in range(count):
 
                element = locator.nth(i)
 
                try:
 
                    if not element.is_visible():
                        continue
 
                    text = (
                        element.get_attribute("title")
                        or element.inner_text()
                        or ""
                    ).strip()
 
                    text = " ".join(text.split())
 
                    if not text:
                        continue
 
                    key = (
                        selector,
                        text,
                        i
                    )
 
                    if key not in seen:
 
                        seen.add(key)
 
                        elements.append(element)
 
                except Exception:
                    continue
 
        except Exception:
            continue
 
    return elements
 
 
def expand_all_folders(page):
 
    log.info("  Checking all folders...")
 
    opened = set()
 
    max_iterations = 500
 
    iteration = 0
 
    while iteration < max_iterations:
 
        iteration += 1
 
        clicked_folder = False
 
        # ----------------------------------------------------
        # Find rows containing folder icons
        # ----------------------------------------------------
 
        rows = page.locator("tr")
 
        row_count = rows.count()
 
        for i in range(row_count):
 
            row = rows.nth(i)
 
            try:
 
                if not row.is_visible():
                    continue
 
                # Check whether this row looks like a folder
                folder_icons = row.locator(
                    "svg.fa-folder, "
                    "svg.fa-folder-open, "
                    "[class*='folder']"
                )
 
                if folder_icons.count() == 0:
                    continue
 
                # ------------------------------------------------
                # Get possible folder name from HTML
                # ------------------------------------------------
 
                name = ""
 
                candidates = row.locator(
                    "span[title], "
                    "a[title], "
                    "[data-testid*='name'], "
                    "td"
                )
 
                candidate_count = candidates.count()
 
                for j in range(candidate_count):
 
                    candidate = candidates.nth(j)
 
                    try:
 
                        if not candidate.is_visible():
                            continue
 
                        value = (
                            candidate.get_attribute("title")
                            or candidate.inner_text()
                            or ""
                        ).strip()
 
                        value = " ".join(value.split())
 
                        if value:
 
                            name = value
 
                            break
 
                    except Exception:
                        continue
 
                if not name:
                    continue
 
                # ------------------------------------------------
                # Ignore UI text
                # ------------------------------------------------
 
                ignored = {
                    "file",
                    "public",
                    "servicequotes",
                    "delete",
                    "confirm deletion",
                    "cancel",
                    "add subfolder",
                    "upload files to folder",
                }
 
                if name.lower() in ignored:
                    continue
 
                # ------------------------------------------------
                # Check whether folder is already open
                # ------------------------------------------------
 
                open_icon = row.locator(
                    "svg.fa-folder-open"
                )
 
                closed_icon = row.locator(
                    "svg.fa-folder"
                )
 
                if open_icon.count() > 0:
 
                    continue
 
                if closed_icon.count() == 0:
 
                    continue
 
                # ------------------------------------------------
                # Unique identifier
                # ------------------------------------------------
 
                row_text = " ".join(
                    row.inner_text().split()
                )
 
                identifier = (
                    f"{name}|{row_text}"
                )
 
                if identifier in opened:
                    continue
 
                # ------------------------------------------------
                # Click folder
                # ------------------------------------------------
 
                log.info(
                    "    Opening folder: %s",
                    name
                )
 
                clicked = False
 
                click_targets = [
                    "span[title]",
                    "a[title]",
                    "td:first-child",
                    "span",
                    "a"
                ]
 
                for selector in click_targets:
 
                    try:
 
                        target = row.locator(
                            selector
                        ).first
 
                        if target.count() == 0:
                            continue
 
                        if not target.is_visible():
                            continue
 
                        target.click(
                            timeout=5000
                        )
 
                        clicked = True
 
                        break
 
                    except Exception:
                        continue
 
                if clicked:
 
                    page.wait_for_timeout(
                        FOLDER_CLICK_SETTLE_MS
                    )
 
                    opened.add(identifier)
 
                    clicked_folder = True
 
                    break
 
            except Exception:
                continue
 
        # ----------------------------------------------------
        # If a folder was opened, scan again
        # ----------------------------------------------------
 
        if clicked_folder:
 
            continue
 
        # ----------------------------------------------------
        # No unopened folder found
        # ----------------------------------------------------
 
        break
 
    log.info(
        "  Folders opened: %d",
        len(opened)
    )
 
 
# ============================================================
# EXTRACT DOCUMENTS DIRECTLY FROM HTML
# ============================================================
 
def get_document_names_from_html(page) -> list[str]:
 
    """
    Extract document names from the actual rendered HTML.
 
    This does NOT depend on:
        PDF
        JPEG
        PNG
        DOCX
        XLSX
 
    Any filename exposed by the Uptick document HTML can be
    collected.
    """
 
    names = []
 
    seen = set()
 
    # --------------------------------------------------------
    # Get complete HTML
    # --------------------------------------------------------
 
    try:
 
        html = page.locator(
            "body"
        ).inner_html()
 
    except Exception as e:
 
        log.error(
            "Could not read page HTML: %s",
            e
        )
 
        return []
 
    # --------------------------------------------------------
    # Use DOM evaluation to inspect visible document rows
    # --------------------------------------------------------
 
    try:
 
        extracted = page.locator(
            "tr"
        ).evaluate_all(
            """
            rows => rows.map(row => {
 
                const text = row.innerText || "";
 
                const titles = Array.from(
                    row.querySelectorAll("[title]")
                ).map(el => el.getAttribute("title"));
 
                const links = Array.from(
                    row.querySelectorAll("a")
                ).map(el =>
                    el.getAttribute("title") ||
                    el.innerText ||
                    el.getAttribute("href")
                );
 
                return {
                    text: text,
                    titles: titles,
                    links: links
                };
 
            })
            """
        )
 
    except Exception as e:
 
        log.warning(
            "DOM extraction failed: %s",
            e
        )
 
        extracted = []
 
    # --------------------------------------------------------
    # Process every row
    # --------------------------------------------------------
 
    for entry in extracted:
 
        try:
 
            row_text = " ".join(
                str(entry.get("text", "")).split()
            )
 
            titles = entry.get(
                "titles",
                []
            )
 
            links = entry.get(
                "links",
                []
            )
 
            # ------------------------------------------------
            # Determine if this is a folder
            # ------------------------------------------------
 
            row_lower = row_text.lower()
 
            is_folder = False
 
            if any(
                word in row_lower
                for word in [
                    "public",
                    "servicequotes"
                ]
            ):
 
                # Don't automatically classify every row
                # containing these words as a folder.
                pass
 
            # Check HTML itself for folder icons
            html_row = ""
 
            try:
 
                # Not relying on filenames/extensions.
                # We use DOM structure instead.
                pass
 
            except Exception:
                pass
 
            # ------------------------------------------------
            # Candidate values
            # ------------------------------------------------
 
            candidates = []
 
            candidates.extend(titles)
 
            candidates.extend(links)
 
            # Also inspect individual elements
            # ------------------------------------------------
 
            for candidate in candidates:
 
                if not candidate:
                    continue
 
                candidate = str(
                    candidate
                ).strip()
 
                candidate = " ".join(
                    candidate.split()
                )
 
                if not candidate:
                    continue
 
                lower = candidate.lower()
 
                # Ignore UI controls
                if lower in {
                    "file",
                    "public",
                    "servicequotes",
                    "delete",
                    "cancel",
                    "confirm deletion",
                    "add subfolder",
                    "upload files to folder",
                    "drop files upload to this folder",
                }:
                    continue
 
                if "drop files" in lower:
                    continue
 
                if "upload to this folder" in lower:
                    continue
 
                if "confirm deletion" in lower:
                    continue
 
                # Ignore URLs
                if lower.startswith(
                    (
                        "http://",
                        "https://",
                        "/"
                    )
                ):
                    continue
 
                # ------------------------------------------------
                # Do NOT check extension.
                # ------------------------------------------------
 
                # If it looks like a document filename,
                # collect it.
                #
                # Examples:
                # test.pdf
                # image.jpeg
                # report.docx
                # file.xyz
                # something.with.multiple.dots
                #
                # There is intentionally no extension whitelist.
 
                if "." in candidate:
 
                    if candidate not in seen:
 
                        seen.add(candidate)
 
                        names.append(candidate)
 
        except Exception:
            continue
 
    # --------------------------------------------------------
    # Additional direct DOM extraction
    # --------------------------------------------------------
 
    try:
 
        all_title_elements = page.locator(
            "[title]"
        )
 
        count = all_title_elements.count()
 
        for i in range(count):
 
            try:
 
                element = all_title_elements.nth(i)
 
                if not element.is_visible():
                    continue
 
                title = (
                    element.get_attribute(
                        "title"
                    )
                    or ""
                ).strip()
 
                title = " ".join(
                    title.split()
                )
 
                if not title:
                    continue
 
                lower = title.lower()
 
                # Ignore known UI text
                if lower in {
                    "file",
                    "public",
                    "servicequotes",
                    "delete",
                    "cancel",
                    "confirm deletion",
                    "add subfolder",
                    "upload files to folder",
                }:
                    continue
 
                if "drop files" in lower:
                    continue
 
                if "upload to this folder" in lower:
                    continue
 
                # ------------------------------------------------
                # No extension whitelist
                # ------------------------------------------------
 
                if "." in title:
 
                    if title not in seen:
 
                        seen.add(title)
 
                        names.append(title)
 
            except Exception:
                continue
 
    except Exception:
        pass
 
    return names
 
 
# ============================================================
# SAVE DEBUG HTML
# ============================================================
 
def save_debug_html(page, property_id):
 
    try:
 
        filename = (
            f"debug_property_{property_id}.html"
        )
 
        html = page.locator(
            "body"
        ).inner_html()
 
        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as f:
 
            f.write(html)
 
        log.info(
            "  Debug HTML saved: %s",
            filename
        )
 
    except Exception as e:
 
        log.warning(
            "Could not save debug HTML: %s",
            e
        )
 
 
# ============================================================
# PROCESS ONE PROPERTY
# ============================================================
 
def process_property(
    page,
    property_id: str
):
 
    log.info(
        "\n" + "=" * 70
    )
 
    log.info(
        "PROPERTY ID: %s",
        property_id
    )
 
    log.info(
        "=" * 70
    )
 
    documents_url = (
        f"{UPTICK_URL}/properties/"
        f"{property_id}/view/documents/"
    )
 
    try:
 
        # ----------------------------------------------------
        # Open property Documents page
        # ----------------------------------------------------
 
        page.goto(
            documents_url,
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS
        )
 
        page.wait_for_timeout(
            PAGE_LOAD_SETTLE_MS
        )
 
        # ----------------------------------------------------
        # Property name
        # ----------------------------------------------------
 
        property_name = get_property_name(
            page
        )
 
        log.info(
            "Property Name: %s",
            property_name
        )
 
        # ----------------------------------------------------
        # Open every folder
        # ----------------------------------------------------
 
        expand_all_folders(
            page
        )
 
        # Give Uptick time to render
        # newly opened nested folders
        page.wait_for_timeout(
            1500
        )
 
        # ----------------------------------------------------
        # Extract documents from HTML/DOM
        # ----------------------------------------------------
 
        documents = get_document_names_from_html(
            page
        )
 
        # ----------------------------------------------------
        # Remove duplicates
        # ----------------------------------------------------
 
        documents = list(
            dict.fromkeys(documents)
        )
 
        # ----------------------------------------------------
        # Logging
        # ----------------------------------------------------
 
        log.info(
            "Documents found: %d",
            len(documents)
        )
 
        for document in documents:
 
            log.info(
                "  ✓ %s",
                document
            )
 
        if not documents:
 
            log.warning(
                "  ✗ No documents detected."
            )
 
        return (
            property_name,
            documents
        )
 
    except PWTimeout as e:
 
        log.error(
            "Timeout processing property %s: %s",
            property_id,
            e
        )
 
        return None
 
    except Exception as e:
 
        log.error(
            "ERROR processing property %s: %s",
            property_id,
            e
        )
 
        return None
 
 
# ============================================================
# MAIN
# ============================================================
 
def main():
 
    log.info(
        "=" * 70
    )
 
    log.info(
        "UPTICK DOCUMENT SCANNER"
    )
 
    log.info(
        "=" * 70
    )
 
    # --------------------------------------------------------
    # Check input CSV
    # --------------------------------------------------------
 
    if not Path(
        INPUT_FILE
    ).exists():
 
        log.error(
            "%s not found in %s",
            INPUT_FILE,
            Path.cwd()
        )
 
        return
 
    # --------------------------------------------------------
    # Read properties
    # --------------------------------------------------------
 
    property_ids = get_property_ids()
 
    log.info(
        "Properties found: %d",
        len(property_ids)
    )
 
    if not property_ids:
 
        log.error(
            "No property IDs found."
        )
 
        return
 
    # --------------------------------------------------------
    # Create output
    # --------------------------------------------------------
 
    writer = ResultsWriter(
        OUTPUT_FILE
    )
 
    log.info(
        "\nWriting results to: %s",
        OUTPUT_FILE
    )
 
    # --------------------------------------------------------
    # Start Playwright
    # --------------------------------------------------------
 
    with sync_playwright() as p:
 
        browser = p.chromium.launch(
            headless=False
        )
 
        context = browser.new_context(
            viewport={
                "width": 1400,
                "height": 900
            }
        )
 
        page = context.new_page()
 
        # ----------------------------------------------------
        # Login
        # ----------------------------------------------------
 
        log.info(
            "\nOpening Uptick..."
        )
 
        page.goto(
            UPTICK_URL,
            wait_until="domcontentloaded"
        )
 
        login(page)
 
        log.info(
            "\nLogin confirmed."
        )
 
        # ----------------------------------------------------
        # Process every property
        # ----------------------------------------------------
 
        total = len(
            property_ids
        )
 
        for index, property_id in enumerate(
            property_ids,
            start=1
        ):
 
            log.info(
                "\nPROGRESS: %d/%d",
                index,
                total
            )
 
            result = process_property(
                page,
                property_id
            )
 
            if result is not None:
 
                property_name, documents = result
 
                writer.add(
                    property_id,
                    property_name,
                    documents
                )
 
            else:
 
                writer.add(
                    property_id,
                    "Unknown",
                    ["ERROR"]
                )
 
            # Small pause before next property
            time.sleep(1)
 
        # ----------------------------------------------------
        # Close browser
        # ----------------------------------------------------
 
        browser.close()
 
    # --------------------------------------------------------
    # Close CSV
    # --------------------------------------------------------
 
    writer.close()
 
    log.info(
        "\n" + "=" * 70
    )
 
    log.info(
        "SCAN COMPLETE"
    )
 
    log.info(
        "=" * 70
    )
 
    log.info(
        "\nOutput file:"
    )
 
    log.info(
        "%s",
        Path(
            OUTPUT_FILE
        ).resolve()
    )
 
 
# ============================================================
# RUN
# ============================================================
 
if __name__ == "__main__":
 
    main()