"""Name normalisation shared by importers and lookups. Never join on raw names."""

import re

# Counter-ions and hydrate words that turn a parent drug name into a salt form,
# e.g. "clopidogrel bisulfate" -> "CLOPIDOGREL".
SALT_WORDS = {
    "ACETATE", "BESILATE", "BESYLATE", "BISULFATE", "BITARTRATE", "BROMIDE", "CALCIUM", "CHLORIDE",
    "CITRATE", "DIHYDRATE", "DIHYDROCHLORIDE", "DIPROPIONATE", "DISODIUM", "FUMARATE", "HCL",
    "HEMIFUMARATE", "HEMIHYDRATE", "HYCLATE", "HYDROBROMIDE", "HYDROCHLORIDE", "HYDROGEN", "LACTATE",
    "MAGNESIUM", "MALEATE", "MESILATE", "MESYLATE", "MONOHYDRATE", "NITRATE", "OXALATE", "PHOSPHATE",
    "POTASSIUM", "PROPIONATE", "SODIUM", "SUCCINATE", "SULFATE", "SULPHATE", "TARTRATE", "TOSILATE",
    "TOSYLATE", "TRIHYDRATE", "VALERATE",
}


def norm(value):
    """Upper-case, trim and collapse whitespace, so lookups are case-insensitive on any database."""
    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def strip_salt(value):
    """Drop trailing salt words: "CLOPIDOGREL BISULFATE" -> "CLOPIDOGREL".

    Names that are themselves salts ("MAGNESIUM SULFATE", "SODIUM CHLORIDE") are left unchanged.
    """
    original = norm(value)
    words = original.replace("(", " ").replace(")", " ").split()
    while len(words) > 1 and words[-1] in SALT_WORDS:
        words.pop()
    if len(words) == 1 and words[0] in SALT_WORDS:
        return original
    return " ".join(words)
