"""Commercial and work-management data: contracts, licenses, usage, costs, budget, Jira, Confluence.

Planted: P3 (under/over-used licenses), P4 (renewals & notice deadlines, 18% uplift), P8 (hosting cost jump after
migration), P10 (same product bought via two resellers), P11 (quiet app with large license cost).
Negative controls: non-renewing contract ending in 100 days; license at 74% on a seasonal user base.
Background values are kept clear of rule thresholds so rule findings map exactly to planted items.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sed.synth.catalog import Catalog
from sed.synth.tickets import month_start

PRODUCTS = {
    "Software": ["Enterprise Suite", "Analytics Add-on", "Designer Suite", "Integration Server", "Mobile Module"],
    "SaaS": ["Cloud Subscription", "Premium Tier", "API Package", "Storage Pack"],
    "Managed Services": ["Application Management Services", "L2 Support Retainer", "Monitoring Service"],
    "Hosting": ["Private Cloud Hosting", "Database Hosting", "Backup Service"],
    "Consulting": ["Change Requests Framework", "Advisory Retainer"],
    "Reseller": ["License Resale", "Maintenance Renewal"],
    "Telecom": ["WAN Service", "SD-WAN Sites"],
}
METRICS = ["Named user", "Concurrent user", "Core", "Instance", "Device", "Subscription"]
COST_CATEGORIES = ["Licenses", "Hosting", "Support", "Development"]


@dataclass
class Contract:
    number: str
    vendor: str
    vendor_spelling: str
    app: str | None
    product: str
    start: date
    end: date
    notice_days: int
    auto_renew: bool
    status: str
    annual_value: float
    currency: str
    owner: str
    comments: str
    pattern: str = ""


@dataclass
class License:
    license_id: str
    app: str
    vendor: str
    contract: str | None
    product: str
    metric: str
    entitled: float
    unit_cost: float
    usage: dict[str, tuple[float, float]] = field(default_factory=dict)  # month -> (assigned, active_90d)
    pattern: str = ""


@dataclass
class Commercial:
    contracts: list[Contract]
    licenses: list[License]
    cost_actuals: list[dict[str, Any]]
    budget: list[dict[str, Any]]
    truth: dict[str, Any]


def _spelling(rnd: random.Random, vendor, allow_alias: bool) -> str:
    if allow_alias and vendor.aliases and rnd.random() < 0.5:
        return rnd.choice(vendor.aliases)
    return vendor.name


def build_commercial(cat: Catalog, seed: int, anchor: date, window_start: date) -> Commercial:
    rnd = random.Random(f"{seed}:commercial")
    contracts: list[Contract] = []
    truth: dict[str, Any] = {"P3": [], "P4": [], "P8": {}, "P10": [], "P11": {}, "controls": {}}
    counter = 1

    def new_number() -> str:
        nonlocal counter
        counter += 1
        return f"CTR-{2020 + counter % 6}-{counter:04d}"

    apps_by_name = {a.name: a for a in cat.apps}
    for vendor in cat.vendors:
        for _ in range(rnd.randint(2, 5)):
            app = rnd.choice(cat.apps) if rnd.random() < 0.8 else None
            notice = rnd.choice([30, 60, 90, 180])
            # Background dates stay clear of the rules: end > 200 days away AND notice deadline > 60 days away,
            # or long expired.
            if rnd.random() < 0.12:
                end = anchor - timedelta(days=rnd.randint(90, 400))
                status = rnd.choice(["Expired", "Terminated"])
            else:
                end = anchor + timedelta(days=rnd.randint(max(205, notice + 70), 900))
                status = "Active"
            start = end - timedelta(days=365 * rnd.choice([1, 2, 3]))
            contracts.append(
                Contract(
                    number=new_number(),
                    vendor=vendor.name,
                    vendor_spelling=_spelling(rnd, vendor, True),
                    app=app.name if app else None,
                    product=rnd.choice(PRODUCTS[vendor.vtype]),
                    start=start,
                    end=end,
                    notice_days=notice,
                    auto_renew=rnd.random() < 0.4,
                    status=status,
                    annual_value=round(min(600_000, max(12_000, rnd.lognormvariate(11.2, 0.8))), -2),
                    currency=rnd.choices(["EUR", "USD", "GBP"], weights=[70, 25, 5])[0],
                    owner=rnd.choice(cat.people[240:]),
                    comments=rnd.choice(
                        [
                            "",
                            "Price review clause at renewal.",
                            "Includes 8x5 support.",
                            "Volume tier 2.",
                            f"Negotiated by {rnd.choice(cat.people)} in last cycle.",
                        ]
                    ),
                )
            )

    # P4: six contracts ending within 120 days; two auto-renew with notice deadline <= 21 days; one with uplift.
    p4_specs = [
        ("Solstice Software", "Northstar CRM", 45, 30, True, "P4-auto-renew-deadline"),
        ("Tessellate Software", "Atlas PLM", 105, 90, True, "P4-auto-renew-deadline"),
        ("Harborline Hosting", "Nimbus WMS", 70, 60, False, "P4-uplift"),
        ("Quanta Cloud", "Lumen BI", 110, 30, False, "P4"),
        ("Pinecone Analytics", "Sextant Reporting", 88, 60, False, "P4"),
        ("Corvid Security", "Keyring Identity", 116, 90, False, "P4"),
    ]
    for vendor_name, app_name, end_in, notice, auto, tag in p4_specs:
        if tag == "P4-auto-renew-deadline":
            end_in = notice + rnd.randint(8, 21)
        vendor = cat.vendor(vendor_name)
        c = Contract(
            new_number(),
            vendor.name,
            _spelling(rnd, vendor, True),
            app_name,
            PRODUCTS[vendor.vtype][0],
            anchor + timedelta(days=end_in) - timedelta(days=365 * 3),
            anchor + timedelta(days=end_in),
            notice,
            auto,
            "Active",
            180_000 + 20_000 * len(truth["P4"]),
            "EUR",
            rnd.choice(cat.people[240:]),
            "Renewal under discussion." if not auto else "",
            pattern=tag,
        )
        contracts.append(c)
        truth["P4"].append(
            {
                "contract": c.number,
                "end_date": c.end.isoformat(),
                "notice_deadline": (c.end - timedelta(days=c.notice_days)).isoformat(),
                "auto_renew": auto,
                "tag": tag,
            }
        )
    ctl = Contract(
        new_number(),
        "Bluecrest Consulting",
        "Bluecrest Consulting",
        "Forge MES",
        "Advisory Retainer",
        anchor - timedelta(days=265),
        anchor + timedelta(days=100),
        60,
        False,
        "Non-renewing",
        60_000,
        "EUR",
        rnd.choice(cat.people[240:]),
        "Decision taken not to renew.",
        pattern="CTL-non-renewing",
    )
    contracts.append(ctl)
    truth["controls"]["non_renewing_contract"] = ctl.number

    # P10: same product bought through two resellers for the same application.
    for reseller in ("Monarch Resellers", "Umber Resellers"):
        vendor = cat.vendor(reseller)
        c = Contract(
            new_number(),
            vendor.name,
            _spelling(rnd, vendor, True),
            "Blueprint CAD Vault",
            "Tessellate Designer Suite licenses",
            anchor - timedelta(days=200),
            anchor + timedelta(days=530),
            90,
            True,
            "Active",
            95_000,
            "EUR",
            rnd.choice(cat.people[240:]),
            "",
            pattern="P10",
        )
        contracts.append(c)
        truth["P10"].append(c.number)

    # Licenses: 2-5 lines per app; background utilization in [0.76, 0.97].
    licenses: list[License] = []
    lic_counter = 0
    months = [month_start(anchor, k).strftime("%Y-%m") for k in (3, 2, 1)]
    by_app_contracts: dict[str, list[Contract]] = {}
    for c in contracts:
        if c.app and c.status == "Active":
            by_app_contracts.setdefault(c.app, []).append(c)
    for app in cat.apps:
        for _ in range(rnd.randint(2, 5)):
            if app.name == "Quarry Data Catalog":  # P11 app: only the planted license line below
                continue
            lic_counter += 1
            options = by_app_contracts.get(app.name, [])
            contract = rnd.choice(options) if options and rnd.random() < 0.7 else None
            vendor = contract.vendor if contract else (app.vendor or rnd.choice(cat.vendors).name)
            entitled = float(rnd.choice([25, 50, 100, 150, 250, 500, 1000]))
            util = rnd.uniform(0.76, 0.97)
            lic = License(
                f"LIC-{lic_counter:05d}",
                app.name,
                vendor,
                contract.number if contract else None,
                f"{app.name} {rnd.choice(['Standard', 'Professional', 'Enterprise'])}",
                rnd.choice(METRICS),
                entitled,
                round(rnd.uniform(40, 900), 2),
            )
            for m in months:
                active = entitled * min(0.99, max(0.74, util + rnd.uniform(-0.02, 0.02)))
                lic.usage[m] = (round(min(entitled, active * rnd.uniform(1.0, 1.04))), round(active))
            licenses.append(lic)

    def plant_license(
        app_name: str, entitled: float, unit_cost: float, util: float, assigned_ratio: float, tag: str
    ) -> License:
        nonlocal lic_counter
        lic_counter += 1
        app = apps_by_name[app_name]
        lic = License(
            f"LIC-{lic_counter:05d}",
            app_name,
            app.vendor or "Tessellate Software",
            None,
            f"{app_name} Enterprise",
            "Named user",
            entitled,
            unit_cost,
            pattern=tag,
        )
        for m in months:
            lic.usage[m] = (round(entitled * assigned_ratio), round(entitled * util))
        licenses.append(lic)
        return lic

    for app_name, util in (("Forge MES", 0.31), ("Canvas CPQ", 0.38), ("Archive DMS", 0.44)):
        lic = plant_license(app_name, 200, 500.0, util, util + 0.05, "P3-under")
        truth["P3"].append(
            {"license": lic.license_id, "utilization": util, "idle_cost": round((200 - round(200 * util)) * 500.0, 2)}
        )
    over = plant_license("Northstar CRM", 300, 420.0, 1.08, 1.12, "P3-over")
    truth["P3"].append({"license": over.license_id, "utilization": 1.08, "assigned_ratio": 1.12})
    ctl_lic = plant_license("Pathway Learning", 500, 60.0, 0.74, 0.80, "CTL-seasonal-74")
    truth["controls"]["seasonal_license"] = ctl_lic.license_id
    quiet = plant_license("Quarry Data Catalog", 120, 1500.0, 0.85, 0.9, "P11")
    truth["P11"] = {"app": "Quarry Data Catalog", "license": quiet.license_id, "annual_license_cost": 180_000}

    # Costs: monthly actuals per app x category; budget FY(anchor year) per month. Noise +-6% (rule is +25%).
    actuals: list[dict[str, Any]] = []
    budget: list[dict[str, Any]] = []
    month_list: list[date] = []
    d = date(window_start.year, window_start.month, 1)
    while d < date(anchor.year, anchor.month, 1):
        month_list.append(d)
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    p8_start = month_start(anchor, 2)
    uplift_contract = next(c for c in contracts if c.pattern == "P4-uplift")
    for app in cat.apps:
        base = {cat_: rnd.uniform(1_500, 18_000) for cat_ in COST_CATEGORIES}
        if app.name == "Quarry Data Catalog":
            base["Licenses"] = 15_000.0
        vendor_for = {
            "Licenses": app.vendor or "Lumina Licensing",
            "Hosting": "Harborline Hosting",
            "Support": "Nordwind Managed Services" if app.l2_group.startswith("NWD") else "Keel Support Services",
            "Development": "Bluecrest Consulting",
        }
        for cat_name, amount in base.items():
            for m in month_list:
                value = amount * rnd.uniform(0.95, 1.05)
                if app.name == "Atlas PLM" and cat_name == "Hosting" and m >= p8_start:
                    value = amount * 1.6
                if app.name == uplift_contract.app and cat_name == "Hosting" and m.year == anchor.year:
                    value = amount * 1.18
                actuals.append(
                    {
                        "application": app.name,
                        "vendor": vendor_for[cat_name],
                        "category": cat_name,
                        "cost_center": app.cost_center,
                        "month": m,
                        "amount": round(value, 2),
                    }
                )
            for month in range(1, 13):
                budget.append(
                    {
                        "application": app.name,
                        "vendor": vendor_for[cat_name],
                        "category": cat_name,
                        "cost_center": app.cost_center,
                        "period": f"{anchor.year}-{month:02d}",
                        "amount": round(amount, 2),
                    }
                )
    truth["P8"] = {"app": "Atlas PLM", "category": "Hosting", "from_month": p8_start.strftime("%Y-%m"), "factor": 1.6}
    truth["P4_uplift"] = {"contract": uplift_contract.number, "app": uplift_contract.app, "factor": 1.18}
    return Commercial(contracts, licenses, actuals, budget, truth)


JIRA_PROJECTS = [
    ("ORN", "Orion ERP"),
    ("LDG", "Ledgerline Finance"),
    ("PLM", "Atlas PLM"),
    ("CRM", "Northstar CRM"),
    ("WMS", "Nimbus WMS"),
    ("BI", "Lumen BI"),
]


def build_jira(cat: Catalog, seed: int, anchor: date, window_start: date, scale: float) -> list[dict[str, Any]]:
    issues = []
    days = (anchor - window_start).days
    start_dt = datetime(window_start.year, window_start.month, window_start.day)
    for key, app_name in JIRA_PROJECTS:
        rnd = random.Random(f"{seed}:jira:{key}")
        count = 500
        for i in range(1, count + 1):
            created = start_dt + timedelta(
                days=int(days * (i - 1) / count), hours=rnd.randint(8, 17), minutes=rnd.randrange(60)
            )
            done = created + timedelta(days=rnd.randint(2, 60))
            resolved = done if done.date() < anchor and rnd.random() < 0.85 else None
            status = "Done" if resolved else rnd.choice(["To Do", "In Progress", "In Review"])
            sprint_no = (created - start_dt).days // 14 + 1
            issues.append(
                {
                    "Issue key": f"{key}-{i}",
                    "Project key": key,
                    "Project name": app_name,
                    "Summary": rnd.choice(["Improve", "Fix", "Automate", "Upgrade"])
                    + " "
                    + rnd.choice(
                        [
                            "posting performance",
                            "approval workflow",
                            "report export",
                            "interface monitoring",
                            "user onboarding",
                            "data quality checks",
                        ]
                    ),
                    "Issue Type": rnd.choices(["Story", "Bug", "Task", "Epic"], weights=[50, 25, 20, 5])[0],
                    "Status": status,
                    "Status Category": "Done" if resolved else "In Progress" if status != "To Do" else "To Do",
                    "Priority": rnd.choice(["Medium", "High", "Low"]),
                    "Assignee": rnd.choice(cat.agents),
                    "Created": created,
                    "Updated": resolved or created + timedelta(days=1),
                    "Resolved": resolved,
                    "Sprint": [f"{key} Sprint {sprint_no}"]
                    + ([f"{key} Sprint {sprint_no + 1}"] if rnd.random() < 0.15 else []),
                    "Labels": rnd.sample(
                        ["backend", "ux", "tech-debt", "compliance", "quick-win"], k=rnd.randint(0, 2)
                    ),
                    "Component/s": [app_name] if rnd.random() < 0.6 else [],
                    "Fix Version/s": f"{anchor.year}.{(created.month - 1) // 3 + 1}" if resolved else "",
                    "Parent": f"{key}-{max(1, i - rnd.randint(1, 20))}" if rnd.random() < 0.3 else "",
                    "Custom field (Story Points)": rnd.choice([1, 2, 3, 5, 8, 13]),
                }
            )
    return issues


CONFLUENCE_SPACES = {
    "ORION": (
        "Orion ERP",
        [
            ("Month-end closing runbook", ["runbook", "orion-erp"]),
            ("Release notes 2026.2", ["release-notes", "orion-erp"]),
            ("Interface landscape overview", ["architecture", "orion-erp"]),
        ],
    ),
    "LEDGER": (
        "Ledgerline Finance",
        [
            ("Period opening procedure", ["runbook", "ledgerline-finance"]),
            ("Roadmap 2026", ["roadmap", "ledgerline-finance"]),
        ],
    ),
    "HRCORE": (
        "Atlas HR Core",
        [
            ("How to reset your Atlas HR Core password", ["kb", "how-to", "atlas-hr-core"]),
            ("Requesting access to Atlas HR Core", ["kb", "atlas-hr-core"]),
        ],
    ),
}


def build_confluence(cat: Catalog, seed: int, anchor: date) -> dict[str, list[dict[str, Any]]]:
    rnd = random.Random(f"{seed}:confluence")
    spaces: dict[str, list[dict[str, Any]]] = {}
    page_id = 4_100_000
    for key, (app_name, pages) in CONFLUENCE_SPACES.items():
        spaces[key] = []
        for title, labels in pages:
            page_id += rnd.randint(11, 999)
            author = rnd.choice(cat.agents)
            spaces[key].append(
                {
                    "page_id": page_id,
                    "title": title,
                    "labels": labels,
                    "author": author,
                    "modified": anchor - timedelta(days=rnd.randint(3, 200)),
                    "body": f"{title} for {app_name}. Steps: 1) check the dashboard 2) follow the checklist. "
                    f"Contact {author} for questions.",
                }
            )
    return spaces
