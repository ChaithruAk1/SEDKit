"""Fictional master data: application families, apps, vendors (with alias spellings), groups, people, CIs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from faker import Faker

FAMILIES: dict[str, list[str]] = {
    "Finance": [
        "Orion ERP",
        "Ledgerline Finance",
        "Tessera Treasury",
        "Quill Expenses",
        "Abacus Consolidation",
        "Meridian Tax",
        "Cobalt Billing",
        "Tally Asset Register",
    ],
    "Supply Chain": [
        "Nimbus WMS",
        "Cargo Planner",
        "Vector TMS",
        "Harbor Procurement",
        "Relay EDI Hub",
        "Tidewater Inventory",
        "Keystone Supplier Portal",
    ],
    "Manufacturing": [
        "Forge MES",
        "Anvil Quality",
        "Pulse OEE",
        "Helix LIMS",
        "Crane Maintenance",
        "Sparrow Scheduling",
        "Lattice Recipes",
        "Beacon Traceability",
    ],
    "HR": [
        "Atlas HR Core",
        "Pathway Learning",
        "Clockwise Time",
        "Summit Recruiting",
        "Compass Benefits",
        "Echo Surveys",
        "Mosaic Talent",
        "Keyring Identity",
    ],
    "Sales & CRM": [
        "Northstar CRM",
        "Quota Planner",
        "Canvas CPQ",
        "Signal Marketing",
        "Parcel Order Hub",
        "Rally Partner Portal",
        "Prism Pricing",
    ],
    "Engineering": [
        "Atlas PLM",
        "Blueprint CAD Vault",
        "Gauge Test Bench",
        "Circuit Library",
        "Drafting Hub",
        "Stratus Simulation",
        "Nexus Requirements",
    ],
    "Collaboration": [
        "Waypoint Intranet",
        "Loop Messaging",
        "Archive DMS",
        "Pinboard Wiki",
        "Relay Meetings",
        "Vault eSign",
        "Courier Mailroom",
    ],
    "Data & Analytics": [
        "Lumen BI",
        "Delta Lake Platform",
        "Sextant Reporting",
        "Pipeline ETL",
        "Horizon Forecasting",
        "Metric Hub",
        "Quarry Data Catalog",
        "Glass Dashboards",
    ],
}

# name, type, tier, sla_target, alias spellings used in some workbooks (P12 alias chaos)
VENDORS: list[tuple[str, str, str, float, list[str]]] = [
    (
        "Nordwind Managed Services",
        "Managed Services",
        "Strategic",
        0.95,
        ["Nordwind Managed Services GmbH", "NORDWIND MS"],
    ),
    ("Bluecrest Consulting", "Consulting", "Preferred", 0.92, []),
    ("Tessellate Software", "Software", "Strategic", 0.95, ["Tesselate Software"]),
    ("Harborline Hosting", "Hosting", "Strategic", 0.97, ["Harborline Hosting Ltd", "Harbourline Hosting"]),
    ("Quanta Cloud", "SaaS", "Strategic", 0.97, ["Quanta Cloud Inc.", "QuantaCloud"]),
    ("Meridian Systems Integration", "Consulting", "Preferred", 0.93, []),
    ("Pinecone Analytics", "Software", "Tactical", 0.92, []),
    ("Ironbridge Networks", "Telecom", "Preferred", 0.96, []),
    ("Solstice Software", "Software", "Preferred", 0.94, ["Solstice Software SAS", "Solstice SW"]),
    ("Corvid Security", "Software", "Preferred", 0.95, []),
    ("Lumina Licensing", "Reseller", "Tactical", 0.90, []),
    ("Orbital Data Centers", "Hosting", "Strategic", 0.98, []),
    ("Keel Support Services", "Managed Services", "Preferred", 0.93, []),
    ("Verdant Outsourcing", "Managed Services", "Tactical", 0.92, []),
    ("Arcadia Apps", "SaaS", "Tactical", 0.94, []),
    ("Brightpath Training", "Consulting", "Tactical", 0.90, []),
    ("Cinder Labs", "Software", "Tactical", 0.92, []),
    ("Driftwood IT", "Consulting", "Tactical", 0.90, []),
    ("Emberline Telecom", "Telecom", "Preferred", 0.96, []),
    ("Foxglove Consulting", "Consulting", "Tactical", 0.90, []),
    ("Granite Hosting", "Hosting", "Preferred", 0.97, []),
    ("Halcyon SaaS", "SaaS", "Preferred", 0.95, []),
    ("Indigo Print Services", "Managed Services", "Tactical", 0.90, []),
    ("Juniper Field Services", "Managed Services", "Tactical", 0.91, []),
    ("Kestrel Analytics", "SaaS", "Tactical", 0.94, []),
    ("Larkspur Integration", "Consulting", "Tactical", 0.91, []),
    ("Monarch Resellers", "Reseller", "Tactical", 0.90, ["Monarch Reseller"]),
    ("Nightjar Cyber", "Software", "Preferred", 0.95, []),
    ("Opal Staffing", "Consulting", "Tactical", 0.90, []),
    ("Pelican Logistics Software", "Software", "Preferred", 0.94, []),
    ("Quartzline Engineering", "Software", "Preferred", 0.94, []),
    ("Rookery Data", "SaaS", "Tactical", 0.93, []),
    ("Sable Cloud", "SaaS", "Preferred", 0.96, []),
    ("Thistle Consulting", "Consulting", "Tactical", 0.90, []),
    ("Umber Resellers", "Reseller", "Tactical", 0.90, []),
]

LOCALES = ["en_GB", "fr_FR", "de_DE", "en_US", "es_ES", "it_IT", "nl_NL"]
CRITICALITY = {"high": "1 - most critical", "medium": "2 - somewhat critical", "low": "3 - less critical"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@dataclass
class App:
    app_id: str
    name: str
    family: str
    criticality: str
    lifecycle: str
    cost_center: str
    vendor: str | None
    owner: str
    weight: float
    cis: list[str]
    l2_group: str
    l3_group: str


@dataclass
class Vendor:
    vendor_id: str
    name: str
    vtype: str
    tier: str
    sla_target: float
    aliases: list[str]


@dataclass
class Group:
    name: str
    vendor: str | None
    family: str | None
    members: list[str] = field(default_factory=list)


@dataclass
class Catalog:
    apps: list[App]
    vendors: list[Vendor]
    groups: dict[str, Group]
    people: list[str]
    agents: list[str]
    service_desk: str

    def app(self, name: str) -> App:
        return next(a for a in self.apps if a.name == name)

    def vendor(self, name: str) -> Vendor:
        return next(v for v in self.vendors if v.name == name)


VENDOR_RUN_GROUPS = {
    "NWD-ERP-L2": ("Nordwind Managed Services", "Finance"),
    "NWD-FIN-L2": ("Nordwind Managed Services", "Finance"),
    "NWD-SCM-L2": ("Nordwind Managed Services", "Supply Chain"),
    "KEEL-MFG-L2": ("Keel Support Services", "Manufacturing"),
    "KEEL-ENG-L2": ("Keel Support Services", "Engineering"),
    "HBL-HOSTING-L3": ("Harborline Hosting", None),
    "QNT-SAAS-L2": ("Quanta Cloud", "Sales & CRM"),
    "MSI-DATA-L2": ("Meridian Systems Integration", "Data & Analytics"),
    "MSI-COLLAB-L2": ("Meridian Systems Integration", "Collaboration"),
}

FAMILY_CODES = {
    "Finance": "FIN",
    "Supply Chain": "SCM",
    "Manufacturing": "MFG",
    "HR": "HR",
    "Sales & CRM": "CRM",
    "Engineering": "ENG",
    "Collaboration": "COL",
    "Data & Analytics": "DAT",
}


def build_catalog(seed: int) -> Catalog:
    rng = np.random.default_rng([seed, 1])
    fake = Faker(LOCALES)
    Faker.seed(seed)

    people: list[str] = []
    seen: set[str] = set()
    while len(people) < 400:
        name = f"{fake.first_name()} {fake.last_name()}"
        name = re.sub(r"\s+", " ", name).strip()
        if name.lower() in seen or len(name.split()) < 2:
            continue
        seen.add(name.lower())
        people.append(name)

    vendors = [
        Vendor(f"V{idx + 1:03d}", name, vtype, tier, sla, aliases)
        for idx, (name, vtype, tier, sla, aliases) in enumerate(VENDORS)
    ]

    groups: dict[str, Group] = {"IT-SERVICE-DESK-L1": Group("IT-SERVICE-DESK-L1", None, None)}
    for family, code in FAMILY_CODES.items():
        for level in ("L2", "L3"):
            groups[f"IT-{code}-{level}"] = Group(f"IT-{code}-{level}", None, family)
        for extra in ("INT", "DATA"):
            groups[f"IT-{code}-{extra}"] = Group(f"IT-{code}-{extra}", None, family)
    groups["IT-INFRA-L3"] = Group("IT-INFRA-L3", None, None)
    groups["IT-NETWORK-L3"] = Group("IT-NETWORK-L3", None, None)
    groups["IT-IAM-L2"] = Group("IT-IAM-L2", None, None)
    for gname, (vendor, family) in VENDOR_RUN_GROUPS.items():
        groups[gname] = Group(gname, vendor, family)

    agents = people[:240]
    member_pool = list(agents)
    rng.shuffle(member_pool)
    for idx, group in enumerate(groups.values()):
        size = 8 if group.name == "IT-SERVICE-DESK-L1" else 5
        group.members = [member_pool[(idx * 5 + k) % len(member_pool)] for k in range(size)]

    apps: list[App] = []
    all_names = [(family, name) for family, names in FAMILIES.items() for name in names]
    crit_cycle = ["high"] * 10 + ["medium"] * 25 + ["low"] * 25
    order = rng.permutation(len(all_names))
    crit_for = {all_names[i][1]: crit_cycle[rank] for rank, i in enumerate(order)}
    for forced_high in ("Orion ERP", "Ledgerline Finance", "Atlas PLM", "Northstar CRM", "Keyring Identity"):
        crit_for[forced_high] = "high"
    crit_for["Quarry Data Catalog"] = "low"
    ranks = rng.permutation(len(all_names))
    vendor_software = [v.name for v in vendors if v.vtype in {"Software", "SaaS"}]
    for idx, (family, name) in enumerate(all_names):
        code = FAMILY_CODES[family]
        l2 = f"IT-{code}-L2"
        if name in {"Orion ERP"}:
            l2 = "NWD-ERP-L2"
        elif name in {"Ledgerline Finance", "Tessera Treasury", "Cobalt Billing"}:
            l2 = "NWD-FIN-L2"
        elif name in {"Nimbus WMS", "Harbor Procurement"}:
            l2 = "NWD-SCM-L2"
        elif family == "Manufacturing" and idx % 2 == 0:
            l2 = "KEEL-MFG-L2"
        elif family == "Engineering" and name != "Atlas PLM" and idx % 2 == 1:
            l2 = "KEEL-ENG-L2"
        elif family == "Sales & CRM" and idx % 3 == 0:
            l2 = "QNT-SAAS-L2"
        elif family == "Data & Analytics" and idx % 3 == 0:
            l2 = "MSI-DATA-L2"
        elif family == "Collaboration" and idx % 3 == 1:
            l2 = "MSI-COLLAB-L2"
        rank = int(ranks[idx]) + 1
        weight = 1.0 / rank**1.05
        lifecycle = "Operational"
        if name == "Quarry Data Catalog":
            lifecycle = "Operational"
        elif rank > 55:
            lifecycle = str(rng.choice(["End of Life", "Retiring", "Operational"]))
        s = slug(name)
        cis = [f"{s}-prd-app01", f"{s}-prd-db01"] + ([f"{s}-prd-app02"] if rng.random() < 0.5 else [])
        apps.append(
            App(
                app_id=f"APM{1001000 + idx * 7:07d}",
                name=name,
                family=family,
                criticality=crit_for[name],
                lifecycle=lifecycle,
                cost_center=f"CC{4100 + list(FAMILIES).index(family) * 10}",
                vendor=str(rng.choice(vendor_software)) if rng.random() < 0.6 else None,
                owner=people[240 + idx],
                weight=weight,
                cis=cis,
                l2_group=l2,
                l3_group=f"IT-{code}-L3",
            )
        )
    # Heavy hitters for realistic concentration and for planted patterns.
    for name, boost in (
        ("Orion ERP", 3.0),
        ("Northstar CRM", 2.2),
        ("Waypoint Intranet", 2.0),
        ("Atlas HR Core", 1.8),
        ("Ledgerline Finance", 1.6),
        ("Forge MES", 1.5),
        ("Lumen BI", 1.4),
        ("Atlas PLM", 1.2),
    ):
        app = next(a for a in apps if a.name == name)
        app.weight = max(app.weight, 0.08 * boost)
    return Catalog(apps, vendors, groups, people, agents, "IT-SERVICE-DESK-L1")
