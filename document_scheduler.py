from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import csv
import os
import re
import time
 
 
# ============================================================
# SETTINGS
# ============================================================
 
UPTICK_URL = "https://stokes.onuptick.com"
INPUT_FILE = "properties.csv"
OUTPUT_FILE = "building_schedule_test_output.csv"
 
TEST_LIMIT = 10
 
PAGE_LOAD_WAIT_MS = 2000
NAV_TIMEOUT_MS = 60000
MAX_RETRIES = 3
 
 
# Building fields we need to check
BUILDING_FIELDS = [
    "Building part",
    "Number of tenancies",
    "Building era",
    "BCA class",
    "Size",
    "Construction",
    "Storeys",
    "OCSP#",
    "AGM date",
    "LGA",
    "Applicable standards",
]
 
 
# ============================================================
# HELPERS
# ============================================================
 
def normalize_text(value):
    if value is None:
        return ""
 
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()
 
 
def is_real_building_value(value):
    """
    Rules:
 
    Empty / blank -> EMPTY
    '-' -> EMPTY
 
    Everything else -> AVAILABLE
 
    This means:
    '- above, - below' -> AVAILABLE
    '1 above, - below' -> AVAILABLE
    'N/A' -> AVAILABLE
    'Whole' -> AVAILABLE
    'Post-1994' -> AVAILABLE
    """
 
    value = normalize_text(value)
 
    if value == "":
        return False
 
    if value == "-":
        return False
 
    return True
 
 
# ============================================================
# READ PROPERTY IDS
# ============================================================
 
def read_property_ids():
    property_ids = []
 
    with open(INPUT_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
 
        for row in reader:
 
            # Handles possible "Property ID " header
            property_id = (
                row.get("Property ID")
                or row.get("Property ID ")
                or row.get("property_id")
                or row.get("property id")
            )
 
            if property_id:
                property_ids.append(str(property_id).strip())
 
    return property_ids[:TEST_LIMIT]
 
 
# ============================================================
# LOGIN
# ============================================================
 
def login(page):
 
    print("\nOpening Uptick login page...")
 
    page.goto(
        UPTICK_URL,
        wait_until="domcontentloaded",
        timeout=NAV_TIMEOUT_MS
    )
 
    time.sleep(2)
 
    print("\nPlease login to Uptick manually in the browser.")
    input("After login is complete, press ENTER here to continue...")
 
    print("Login completed. Starting test...\n")
 
 
# ============================================================
# GET PROPERTY NAME
# ============================================================
 
def get_property_name(page):
 
    try:
 
        # From the HTML:
        # <h3>S18/12851 <small>Queens Park Social Rooms (1337)</small></h3>
 
        h3 = page.locator(
            ".property_body h3"
        ).first
 
        if h3.count() > 0:
 
            text = normalize_text(
                h3.inner_text()
            )
 
            # Try small tag first
            small = h3.locator("small")
 
            if small.count() > 0:
                name = normalize_text(
                    small.inner_text()
                )
 
                if name:
                    return name
 
            return text
 
    except Exception:
        pass
 
    return ""
 
 
# ============================================================
# FIND BUILDING / SCHEDULE TAB CONTENT
# ============================================================
 
def get_property_tabs_container(page):
 
    """
    Finds the specific tab navigation containing:
 
        Notes
        Contract
        Building
        Schedules
        Accreditations
 
    Then gets its immediately following .tab-content.
 
    We do NOT click anything.
    """
 
    tab_nav = page.locator(
        "ul.nav.nav-fill.nav-tabs"
    ).filter(
        has=page.get_by_role(
            "button",
            name="Building",
            exact=True
        )
    ).first
 
    if tab_nav.count() == 0:
        return None
 
    # The .tab-content immediately follows this tab navigation.
    tab_content = tab_nav.locator(
        "xpath=following-sibling::div[contains(@class,'tab-content')][1]"
    )
 
    if tab_content.count() == 0:
        return None
 
    return tab_content
 
 
# ============================================================
# EXTRACT BUILDING DATA
# ============================================================
 
def extract_building_data(page):
 
    tab_content = get_property_tabs_container(page)
 
    if tab_content is None:
 
        return {
            "status": "NOT_DETECTED",
            "fields": {},
            "filled_fields": [],
        }
 
    # The HTML structure is:
 
    # 1 = Notes
    # 2 = Contract
    # 3 = Building
    # 4 = Schedules
    # 5 = Accreditations
 
    panes = tab_content.locator(
        ":scope > .tab-pane"
    )
 
    if panes.count() < 3:
 
        return {
            "status": "NOT_DETECTED",
            "fields": {},
            "filled_fields": [],
        }
 
    building_pane = panes.nth(2)
 
    # Extract dt -> following dd
    #
    # Example:
    #
    # <dt>Building part</dt>
    # <dd>Whole</dd>
    #
    # <dt>Number of tenancies</dt>
    # <dd>1</dd>
 
    data = building_pane.evaluate(
        """
        (pane) => {
 
            const result = {};
 
            const dts = pane.querySelectorAll("dl > dt");
 
            dts.forEach(dt => {
 
                const dd = dt.nextElementSibling;
 
                if (
                    dd &&
                    dd.tagName.toLowerCase() === "dd"
                ) {
 
                    const key = dt.innerText.trim();
 
                    const value = dd.innerText.trim();
 
                    result[key] = value;
                }
 
            });
 
            return result;
        }
        """
    )
 
    filled_fields = []
 
    for field in BUILDING_FIELDS:
 
        value = data.get(field, "")
 
        if is_real_building_value(value):
 
            filled_fields.append(field)
 
    if len(filled_fields) > 0:
 
        status = "AVAILABLE"
 
    else:
 
        status = "EMPTY"
 
    return {
        "status": status,
        "fields": data,
        "filled_fields": filled_fields,
    }
 
 
# ============================================================
# EXTRACT SCHEDULE DATA
# ============================================================
 
def extract_schedule_data(page):
 
    tab_content = get_property_tabs_container(page)
 
    if tab_content is None:
 
        return {
            "status": "NOT_DETECTED",
            "details": "",
        }
 
    panes = tab_content.locator(
        ":scope > .tab-pane"
    )
 
    if panes.count() < 4:
 
        return {
            "status": "NOT_DETECTED",
            "details": "",
        }
 
    # 4th pane = Schedules
    schedule_pane = panes.nth(3)
 
    # --------------------------------------------------------
    # First check the exact empty message
    # --------------------------------------------------------
 
    empty_message = schedule_pane.get_by_text(
        "No schedules attached to this site.",
        exact=True
    )
 
    if empty_message.count() > 0:
 
        return {
            "status": "EMPTY",
            "details": "",
        }
 
    # --------------------------------------------------------
    # Otherwise inspect the schedule table
    # --------------------------------------------------------
 
    table = schedule_pane.locator(
        "table"
    ).first
 
    if table.count() == 0:
 
        return {
            "status": "NOT_DETECTED",
            "details": "",
        }
 
    # Get table rows excluding header
    rows = table.locator(
        "tbody tr"
    )
 
    schedule_rows = []
 
    for i in range(rows.count()):
 
        row = rows.nth(i)
 
        # Ignore empty-row message
        row_text = normalize_text(
            row.inner_text()
        )
 
        if not row_text:
            continue
 
        if (
            "No schedules attached to this site."
            in row_text
        ):
            continue
 
        schedule_rows.append(row_text)
 
    if schedule_rows:
 
        return {
            "status": "AVAILABLE",
            "details": " | ".join(schedule_rows),
        }
 
    return {
        "status": "EMPTY",
        "details": "",
    }
 
 
# ============================================================
# PROCESS ONE PROPERTY
# ============================================================
 
def process_property(page, property_id):
 
    property_url = (
        f"{UPTICK_URL}/properties/{property_id}/view/"
    )
 
    print(
        f"\nProcessing Property ID: {property_id}"
    )
 
    for attempt in range(1, MAX_RETRIES + 1):
 
        try:
 
            print(
                f"  Attempt {attempt}/{MAX_RETRIES}"
            )
 
            page.goto(
                property_url,
                wait_until="domcontentloaded",
                timeout=NAV_TIMEOUT_MS
            )
 
            # Give Uptick time to finish rendering
            page.wait_for_timeout(
                PAGE_LOAD_WAIT_MS
            )
 
            # ------------------------------------------------
            # Check that property page loaded
            # ------------------------------------------------
 
            if page.locator(
                ".property_body"
            ).count() == 0:
 
                raise Exception(
                    "Property page container not detected"
                )
 
            # ------------------------------------------------
            # Property name
            # ------------------------------------------------
 
            property_name = get_property_name(page)
 
            # ------------------------------------------------
            # Building
            # ------------------------------------------------
 
            building = extract_building_data(page)
 
            # ------------------------------------------------
            # Schedule
            # ------------------------------------------------
 
            schedule = extract_schedule_data(page)
 
            print(
                f"  Property Name : {property_name}"
            )
 
            print(
                f"  Building      : {building['status']}"
            )
 
            print(
                f"  Filled fields : "
                f"{len(building['filled_fields'])}"
            )
 
            print(
                f"  Schedule      : {schedule['status']}"
            )
 
            return {
                "Property ID": property_id,
                "Property Name": property_name,
                "Building": building["status"],
                "Building Fields Filled": ", ".join(
                    building["filled_fields"]
                ),
                "Schedule": schedule["status"],
                "Schedule Details": schedule["details"],
            }
 
        except PlaywrightTimeoutError as e:
 
            print(
                f"  Timeout on attempt {attempt}: {e}"
            )
 
        except Exception as e:
 
            print(
                f"  Error on attempt {attempt}: {e}"
            )
 
        if attempt < MAX_RETRIES:
 
            time.sleep(2)
 
    return {
        "Property ID": property_id,
        "Property Name": "",
        "Building": "ERROR",
        "Building Fields Filled": "",
        "Schedule": "ERROR",
        "Schedule Details": "",
    }
 
 
# ============================================================
# MAIN
# ============================================================
 
def main():
 
    property_ids = read_property_ids()
 
    if not property_ids:
 
        print(
            "No Property IDs found in properties.csv"
        )
 
        return
 
    print(
        f"Testing first {len(property_ids)} properties..."
    )
 
    results = []
 
    with sync_playwright() as p:
 
        browser = p.chromium.launch(
            headless=False
        )
 
        context = browser.new_context()
 
        page = context.new_page()
 
        page.set_default_timeout(
            NAV_TIMEOUT_MS
        )
 
        # ----------------------------------------------------
        # Login once
        # ----------------------------------------------------
 
        login(page)
 
        # ----------------------------------------------------
        # Process properties
        # ----------------------------------------------------
 
        for property_id in property_ids:
 
            result = process_property(
                page,
                property_id
            )
 
            results.append(result)
 
        # ----------------------------------------------------
        # Close browser
        # ----------------------------------------------------
 
        browser.close()
 
    # --------------------------------------------------------
    # Save output
    # --------------------------------------------------------
 
    fieldnames = [
        "Property ID",
        "Property Name",
        "Building",
        "Building Fields Filled",
        "Schedule",
        "Schedule Details",
    ]
 
    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:
 
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )
 
        writer.writeheader()
 
        writer.writerows(results)
 
    print(
        f"\nCompleted."
    )
 
    print(
        f"Output file: {OUTPUT_FILE}"
    )
 
 
if __name__ == "__main__":
    main()