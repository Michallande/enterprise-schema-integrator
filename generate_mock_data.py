"""Generate messy legacy customer data for testing the schema integrator.

The output is deterministic so demo runs are reproducible. Seeded issues
(row numbers are 0-based, matching pandas indices and DataIssue.row_index):
  - Row 3: missing email
  - Row 5: negative lifetime value
  - Row 7: malformed email (no "@")
  - Row 8: unmapped status value "paused"
  - All rows: non-standard field names and mixed date formats
"""

import json
from pathlib import Path

OUTPUT_FILE = Path(__file__).parent / "messy_customer_data.json"

RECORDS = [
    {
        "cust_ref_id": "CRM-10001",
        "usr_full_name": "Sarah Mitchell",
        "contact_email_addr": "sarah.mitchell@acmecorp.com",
        "reg_date_str": "2023-05-12",
        "total_revenue_usd": 48250.00,
        "client_status": "Active",
    },
    {
        "cust_ref_id": "CRM-10002",
        "usr_full_name": "James O'Connor",
        "contact_email_addr": "j.oconnor@globex.io",
        "reg_date_str": "12/05/2023",
        "total_revenue_usd": "12,400.50",
        "client_status": "active",
    },
    {
        "cust_ref_id": "CRM-10003",
        "usr_full_name": "Priya Raman",
        "contact_email_addr": "priya.raman@initech.com",
        "reg_date_str": "May 12, 2023",
        "total_revenue_usd": 97300.75,
        "client_status": "ACTIVE",
    },
    {
        # Missing email
        "cust_ref_id": "CRM-10004",
        "usr_full_name": "Daniel Kowalski",
        "contact_email_addr": None,
        "reg_date_str": "2022-11-03",
        "total_revenue_usd": 5600.00,
        "client_status": "inactive",
    },
    {
        "cust_ref_id": "CRM-10005",
        "usr_full_name": "  Maria Gonzalez ",
        "contact_email_addr": "maria.gonzalez@umbrella-health.com",
        "reg_date_str": "03/15/2021",
        "total_revenue_usd": 210000,
        "client_status": "Active",
    },
    {
        # Negative lifetime value
        "cust_ref_id": "CRM-10006",
        "usr_full_name": "Kenji Tanaka",
        "contact_email_addr": "kenji.tanaka@stark-ind.jp",
        "reg_date_str": "Jan 8, 2024",
        "total_revenue_usd": -1500.00,
        "client_status": "churned",
    },
    {
        "cust_ref_id": "CRM-10007",
        "usr_full_name": "Emily Clarke",
        "contact_email_addr": "EMILY.CLARKE@WAYNE-ENT.COM",
        "reg_date_str": "2020-07-22",
        "total_revenue_usd": 33120.40,
        "client_status": "Churned",
    },
    {
        # Malformed email (missing "@")
        "cust_ref_id": "CRM-10008",
        "usr_full_name": "Omar Haddad",
        "contact_email_addr": "omar.haddad.cyberdyne.com",
        "reg_date_str": "09/30/2022",
        "total_revenue_usd": 18750.00,
        "client_status": "active",
    },
    {
        # Unmapped status value
        "cust_ref_id": "CRM-10009",
        "usr_full_name": "Lena Fischer",
        "contact_email_addr": "lena.fischer@hooli.de",
        "reg_date_str": "February 14, 2023",
        "total_revenue_usd": 7420.10,
        "client_status": "paused",
    },
    {
        "cust_ref_id": "CRM-10010",
        "usr_full_name": "Marcus Johnson",
        "contact_email_addr": "marcus.johnson@soylent.com",
        "reg_date_str": "2024-02-29",
        "total_revenue_usd": "$64,980.00",
        "client_status": "Inactive",
    },
]


def main() -> None:
    OUTPUT_FILE.write_text(json.dumps(RECORDS, indent=2) + "\n")
    print(f"Wrote {len(RECORDS)} records to {OUTPUT_FILE.name}")


if __name__ == "__main__":
    main()
