"""Word lists that drive the rule-based recognizers.

These are deliberately kept as data (not code) so that tuning precision on a new
corpus is a matter of editing lists rather than rewriting regexes.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Person-name signals
# --------------------------------------------------------------------------

#: Honorifics that make the following title-case run a person with near-certainty.
HONORIFICS: tuple[str, ...] = (
    "Mr", "Mrs", "Ms", "Miss", "Dr", "Prof", "Shri", "Smt", "Sri", "Sh", "Kum",
    "Justice", "Adv", "CA", "CS",
)

#: Job titles / capacities that appear immediately before or after a person's
#: name in corporate filings.  Used both to find names and to score them.
DESIGNATIONS: tuple[str, ...] = (
    "Chairman and Executive Director",
    "Chairman and Managing Director",
    "Joint Managing Director",
    "Managing Director",
    "Whole-time Director",
    "Whole time Director",
    "Executive Director",
    "Independent Director",
    "Non-Executive Director",
    "Nominee Director",
    "Additional Director",
    "Company Secretary and Compliance Officer",
    "Company Secretary",
    "Compliance Officer",
    "Chief Financial Officer",
    "Chief Executive Officer",
    "Chief Operating Officer",
    "Chief Technology Officer",
    "Promoter Selling Shareholder",
    "Selling Shareholder",
    "Key Managerial Personnel",
    "Senior Management Personnel",
    "Contact Person",
    "Authorised Signatory",
    "Proprietor",
    "Designated Partner",
    "Partner",
    "Chairman",
    "Director",
    "Promoter",
)

#: Labels that introduce a person's name in a form/table ("Contact Person: X").
PERSON_LABELS: tuple[str, ...] = (
    "Contact Person",
    "Contact person",
    "Name of the Contact Person",
    "Compliance Officer",
    "Company Secretary",
    "Authorised Signatory",
    "Investor Grievance Contact",
)

# --------------------------------------------------------------------------
# Organisation signals
# --------------------------------------------------------------------------

#: Legal-entity suffixes.  A title-case run ending in one of these is an
#: organisation with very high precision -- far better than model NER on this
#: corpus.  Order matters: longest first so the regex prefers the fuller form.
ORG_SUFFIXES: tuple[str, ...] = (
    "Private Limited",
    "Public Limited Company",
    "Limited Liability Partnership",
    "Family Trust",
    "Chartered Accountants",
    "Pvt. Ltd.",
    "Pvt Ltd",
    "Co. Ltd.",
    "Co Ltd",
    "Limited",
    "Ltd.",
    "Ltd",
    "LLP",
    "L.L.P.",
    "Incorporated",
    "Inc.",
    "Inc",
    "Corporation",
    "Corp.",
    "GmbH",
    "S.A.",
    "N.V.",
    "B.V.",
    "PLC",
    "Trust",
    "& Associates",
    "& Co.",
    "& Sons",
)
# NOTE: deliberately *excluded* as suffixes -- "Bank", "Capital", "Partners",
# "Industries", "Holdings", "Ventures", "Enterprises", "Associates".  They are
# common nouns in this corpus ("Net Working Capital", "our Industries") and cost
# more precision than the recall they buy.  Short forms such as "HDFC Bank" are
# still caught, by propagation from the full "HDFC Bank Limited" mention.

# --------------------------------------------------------------------------
# Address signals
# --------------------------------------------------------------------------

INDIAN_STATES: tuple[str, ...] = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal", "Delhi", "New Delhi", "Jammu and Kashmir", "Ladakh",
    "Puducherry", "Chandigarh",
)

#: Street-type tokens for Western-style postal addresses.
STREET_TYPES: tuple[str, ...] = (
    "Street", "St.", "Avenue", "Ave.", "Road", "Rd.", "Boulevard", "Blvd.",
    "Lane", "Ln.", "Drive", "Dr.", "Court", "Ct.", "Way", "Terrace", "Place",
    "Square", "Highway", "Parkway", "Marg", "Path", "Chowk", "Nagar", "Peth",
)

#: Words that commonly *start* an address block in Indian filings.
ADDRESS_LEAD_HINTS: tuple[str, ...] = (
    "Plot", "Survey", "S. no.", "Gat", "Flat", "Block", "Tower", "Unit",
    "Floor", "Wing", "House", "Building", "Village", "Near", "Opposite",
    "Behind", "Off",
)

#: Labels that introduce an address block.
ADDRESS_LABELS: tuple[str, ...] = (
    "Registered Office",
    "Corporate Office",
    "Address",
    "Registered office",
    "Correspondence Address",
    "Residential Address",
    "Office Address",
)

# --------------------------------------------------------------------------
# Negative evidence
# --------------------------------------------------------------------------

#: Tokens that disqualify a title-case run from being a PERSON.  These are the
#: single biggest lever on precision: spaCy's small English model routinely
#: tags Indian place names, society names and document jargon as PERSON.
PERSON_STOP_TOKENS: frozenset[str] = frozenset(
    w.lower()
    for w in [
        # document / finance jargon
        "Red", "Herring", "Prospectus", "Offer", "Equity", "Shares", "Share",
        "Company", "Companies", "Act", "Board", "Directors", "Director",
        "Committee", "Meeting", "Fiscal", "Financial", "Statements", "Capital",
        "Structure", "Section", "Regulations", "Regulation", "Schedule",
        "Annexure", "Chapter", "Risk", "Factors", "Business", "Management",
        "Objects", "Issue", "Price", "Band", "Bid", "Anchor", "Investor",
        "Investors", "Qualified", "Institutional", "Buyers", "Retail",
        "Individual", "Portion", "Allotment", "Basis", "Escrow", "Bankers",
        "Registrar", "Auditors", "Auditor", "Statutory", "Independent",
        "Chartered", "Accountants", "Limited", "Private", "Trust", "LLP",
        "Bank", "Banking", "Securities", "Exchange", "Stock", "Market",
        "Depository", "Participant", "Underwriters", "Syndicate", "Lead",
        "Manager", "Managers", "Book", "Running", "Public", "Government",
        "Ministry", "Department", "Authority", "Court", "Tribunal", "Notice",
        "Order", "Ticket", "Invoice", "Policy", "Agreement", "Deed", "Trustee",
        "General", "Information", "Summary", "Table", "Contents", "Page",
        "Note", "Notes", "Total", "Amount", "Million", "Billion", "Crore",
        "Lakh", "Rupees", "Fiscal", "Quarter", "Annual", "Report",
        "Corporate", "Identity", "Number", "Registration", "Certificate",
        "Memorandum", "Articles", "Association", "Association's",
        "Insurance", "Employee", "Employees", "Human", "Resources",
        "Products", "Product", "Customers", "Customer", "Suppliers",
        "Supplier", "Revenue", "Operations", "Profit", "Loss", "Assets",
        "Liabilities", "Cash", "Flow", "Working", "Net", "Gross",
        # address / geography nouns that spaCy mislabels
        "Road", "Lane", "Street", "Nagar", "Peth", "Society", "Colony",
        "Apartment", "Apartments", "Building", "Tower", "Floor", "Wing",
        "Block", "Plot", "Survey", "Village", "Taluka", "District", "Pune",
        "Mumbai", "Delhi", "Chennai", "Kolkata", "Bengaluru", "Bangalore",
        "Hyderabad", "Maharashtra", "Gujarat", "Karnataka", "India", "Indian",
        "Gymkhana", "Deccan", "Chakan", "Baner", "Pashan", "Akurdi", "Khed",
        "Shivaji", "Shivajinagar", "Erandawane", "Panchvati", "Model",
        "Campus", "Park", "Centre", "Center", "Complex", "Estate", "Zone",
        "East", "West", "North", "South", "Sector", "Phase", "Cross",
        "Station", "Railway", "Airport", "Hotel", "Hospital", "School",
        "College", "University", "Institute", "Temple", "Church", "Mosque",
        # months / weekdays
        "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December",
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
        "Sunday",
        # Added after reviewing the audit log of the first full run: every one
        # of these produced at least one PERSON false positive.
        "Slip", "Acknowledgement", "Marg", "Measures", "Website", "Managing",
        "Joint", "Bill", "Operational", "House", "Hall", "Bhavan", "Chambers",
        "Compound", "Premises", "Landmark", "Wing", "Annexe", "Gate",
        "Circle", "Junction", "Bypass", "Account", "Accounts", "Form",
        "Forms", "Details", "Detail", "Category", "Categories", "Process",
        "Procedure", "Application", "Applications", "Bidder", "Bidders",
        "Shareholder", "Shareholders", "Promoters", "Subsidiary",
        "Subsidiaries", "Entity", "Entities", "Scheme", "Trustee",
        "Custodian", "Sponsor", "Refund", "Monitoring", "Agency",
        "Materiality", "Threshold", "Metric", "Metrics", "Ratio", "Margin",
        "Turnover", "Inventory", "Receivable", "Receivables", "Payable",
        "Payables", "Borrowings", "Guarantee", "Guarantees", "Indebtedness",
        "Promoter", "Group", "Branch", "Key", "Managerial", "Personnel",
        "Mutual", "Fund", "Funds", "Photo", "Voltaic", "Mega", "Volt",
        "Amperes", "Reference", "Rate", "Secondary", "Transfer", "Facility",
        "Showroom", "Wilful", "Defaulter", "Parents", "Our", "Each", "Their",
        "Non-Executive", "Non-Independent", "Gram", "Jyoti", "Yojana",
        "Kisan", "Urja", "Suraksha", "Pradhan", "Mantri", "Bharat", "Deen",
        "Dayal", "Upadhyaya", "Solar", "Wind", "Energy", "Power", "Grid",
    ]
)

#: Full strings never treated as a PERSON even if every token passes.
PERSON_STOP_PHRASES: frozenset[str] = frozenset(
    s.lower()
    for s in [
        "Book Running Lead Manager",
        "Red Herring Prospectus",
        "Our Company",
        "Our Promoters",
        "Equity Shares",
        "Draft Red Herring",
        "Selling Shareholder",
        "Stock Exchange",
    ]
)

#: Words that, when present, make a "date" a business date rather than a DOB.
NON_DOB_DATE_CONTEXT: tuple[str, ...] = (
    "dated", "as on", "as at", "with effect from", "bid/offer", "fiscal",
    "financial year", "certificate", "agreement", "meeting", "resolution",
    "filed", "listing", "allotment",
)

#: Words that anchor a date as a date of birth.
DOB_CONTEXT: tuple[str, ...] = (
    "date of birth", "dob", "d.o.b", "born on", "born", "birth date",
    "birthdate", "date of birth of",
)
