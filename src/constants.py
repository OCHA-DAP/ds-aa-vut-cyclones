from pathlib import Path

FJI_CRS = "+proj=longlat +ellps=WGS84 +lon_wrap=180 +datum=WGS84 +no_defs"
VUT_CENTROID_LAT = -16.20990
VUT_CENTROID_LON = 167.71990
INPUT_DIR = Path("inputs")
OUTPUT_DIR = Path("outputs")
CAT2COLOR = (
    (5, "rebeccapurple"),
    (4, "crimson"),
    (3, "orange"),
    (2, "limegreen"),
    (1, "dodgerblue"),
    (0, "gray"),
)
TEMPLATES_DIR = Path("src/email/templates")
STATIC_DIR = Path("src/email/static")
INFO_EMAIL_DISTANCE_THRESHOLD = 1000

LOCAL_TIMEZONE = "Pacific/Efate"

PAM = "2015066S08170"
HAROLD = "2020092S09155"
JUDY = "2023055S14184"
KEVIN = "2023059S15149"
LOLA = "2023292S03172"

CERF_SIDS = [
    PAM,
    HAROLD,
    JUDY,
    KEVIN,
    LOLA,
]
PROJECT_PREFIX = "ds-aa-vut-cyclones"
