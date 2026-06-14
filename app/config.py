import os
import boto3
from datetime import timedelta

# ── Flask ──────────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-only-change-in-production')
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
PERMANENT_SESSION_LIFETIME = timedelta(hours=24)

# ── PostgreSQL ─────────────────────────────────────────────────────────────────
DB_CONFIG = {
    'host':     os.getenv('DB_HOST',     'localhost'),
    'database': os.getenv('DB_NAME',     'vibe_db'),
    'user':     os.getenv('DB_USER',     'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'port':     os.getenv('DB_PORT',     '5432'),
}

# ── AWS / Athena ───────────────────────────────────────────────────────────────
ATHENA_DATABASE   = os.getenv('ATHENA_DATABASE',   'jejak-mappro-demo')
S3_STAGING_DIR    = os.getenv('S3_STAGING_DIR',    's3://jejak-mappro-demo/3W-data/athena-query-results/')
AWS_REGION        = os.getenv('AWS_REGION',         'ap-southeast-1')
S3_BUCKET         = os.getenv('S3_BUCKET',          'jejak-mappro-demo')

ATHENA_CACHE_SETTINGS = {
    'max_cache_seconds':           604800,
    'max_cache_query_inspections': 500,
}

def make_aws_session():
    return boto3.Session(region_name=AWS_REGION)

# ── Metabase ───────────────────────────────────────────────────────────────────
METABASE_SITE_URL  = os.environ.get('METABASE_URL',        'http://52.221.228.202:3000')
METABASE_SECRET_KEY = os.environ.get('METABASE_SECRET_KEY', '')

# ── LiteLLM / AI ──────────────────────────────────────────────────────────────
LITELLM_API_KEY  = os.environ.get('LITELLM_API_KEY', '')
LITELLM_API_BASE = os.environ.get('LITELLM_API_BASE', 'https://gateway.ai.celcomdigi.tech/')
LITELLM_MODEL    = os.environ.get('LITELLM_MODEL',    'litellm_proxy/global.anthropic.claude-sonnet-4-20250514-v1:0')

# ── Pricing ────────────────────────────────────────────────────────────────────
PRICING_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'capex_pricing.json')

DEFAULT_PRICING = {
    "EQ": {
        "Accelerate NIC":                       {"price": 65000.00, "min": 50000.00, "max": 80000.00},
        "Add Layer":                            {"price": 30000.00, "min": 10000.00, "max": 50000.00},
        "Add Sector IBC":                       {"price": 20000.00, "min":  1000.00, "max": 40000.00},
        "Add Sector Outdoor":                   {"price": 40000.00, "min": 10000.00, "max": 70000.00},
        "BW Upg":                               {"price": 25000.00, "min":  1000.00, "max": 50000.00},
        "Bi-Sect Antenna + Accessory":          {"price": 15000.00, "min":  1000.00, "max": 30000.00},
        "Bi-Sect Radio":                        {"price": 35000.00, "min": 10000.00, "max": 60000.00},
        "MM":                                   {"price": 60000.00, "min": 20000.00, "max":100000.00},
        "NNS":                                  {"price":290000.00, "min": 80000.00, "max":500000.00},
        "Split Omni to Sector":                 {"price":225000.00, "min": 50000.00, "max":400000.00},
        "Swap all Sector Radio Ericsson to ZTE":{"price":275000.00, "min": 50000.00, "max":500000.00},
    },
    "ES": {
        "Accelerate NIC":                             {"price": 26000.00, "min":  3000.00, "max": 50000.00},
        "Add Layer":                                  {"price": 32000.00, "min":  5000.00, "max": 60000.00},
        "Add Sector IBC":                             {"price": 27000.00, "min":  5000.00, "max": 50000.00},
        "Add Sector Outdoor":                         {"price": 32000.00, "min":  5000.00, "max": 60000.00},
        "BW Upg":                                     {"price": 25000.00, "min":   850.00, "max": 50000.00},
        "Bi-Sect":                                    {"price": 34000.00, "min":  9000.00, "max": 60000.00},
        "Dismantle":                                  {"price": 39000.00, "min":  9510.00, "max": 70000.00},
        "MM":                                         {"price": 35000.00, "min":  9820.00, "max": 60000.00},
        "NNS":                                        {"price": 40000.00, "min": 10000.00, "max": 70000.00},
        "Split Omni to Sector":                       {"price": 40000.00, "min":  9810.00, "max": 70000.00},
        "Swap all sector radio Ericsson to ZTE":      {"price": 41000.00, "min":  9910.00, "max": 72100.00},
    },
}

# ── RAM Cache ──────────────────────────────────────────────────────────────────
MAX_CACHE_SIZE = 20

# ── Malaysia Holidays ──────────────────────────────────────────────────────────
from datetime import datetime

MALAYSIA_HOLIDAYS = {
    datetime(2026, 1,  1): "New Year",
    datetime(2026, 2,  1): "Federal Territory",
    datetime(2026, 2, 17): "CNY",
    datetime(2026, 3, 20): "Hari Raya Aidilfitri",
    datetime(2026, 5,  1): "Labour Day",
    datetime(2026, 5, 27): "Hari Raya Haji",
    datetime(2026, 8, 31): "Merdeka",
    datetime(2026, 9, 16): "Malaysia Day",
    datetime(2026,12, 25): "Christmas",
}

# ── Rollout ────────────────────────────────────────────────────────────────────
ROLLOUT_CHECKPOINTS_DEF = [
    ('CP/MS-1.0',  'Inputs and triggering',   'Pre-work',       1),
    ('CP/MS-1.1',  'Approvals',               'Pre-work',       2),
    ('CP/MS-1.2',  'Sub-Con selection',        'Pre-work',       3),
    ('CP/MS-2.0',  'Review / Approval TSS',   'Pre-work',       4),
    ('CP/MS-2.1',  'Presentation to MNO',     'Pre-work',       5),
    ('CP/MS-2.2',  'Tenancy agreement',        'Pre-work',       6),
    ('CP/MS-2.3',  'OSA',                      'Pre-work',       7),
    ('CP/MS-2.4',  'PBT approval',             'Pre-work',       8),
    ('CP/MS-2.5',  'Soil test',                'Pre-work',       9),
    ('CP/MS-3.0',  'Foundation',               'Implementation', 10),
    ('CP/MS-3.1',  'Tower erection',           'Implementation', 11),
    ('CP/MS-3.2',  'CME',                      'Implementation', 12),
    ('CP/MS-3.3',  'Power system',             'Implementation', 13),
    ('CP/MS-3.4',  'Backhaul readiness',       'Implementation', 14),
    ('CP/MS-3.5',  'Equipment delivery',       'Implementation', 15),
    ('CP/MS-3.6',  'System integration',       'Implementation', 16),
    ('CP/MS-3.7',  'Final acceptance (FAT)',    'Implementation', 17),
    ('CP/MS-3.8',  'RFS',                      'Implementation', 18),
    ('CP/MS-3.9',  'H/O to operations',        'Implementation', 19),
    ('CP/MS-3.10', 'NOC monitoring',           'Implementation', 20),
]

ROLLOUT_ROLES = [
    'Project Manager', 'USPD Approver', 'State Office Approver',
    'Site Engineer', 'Sub-Con', 'NOC Engineer', 'DUSP Approver', 'Observer',
]

ROLLOUT_UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads', 'rollout')
os.makedirs(ROLLOUT_UPLOAD_FOLDER, exist_ok=True)
