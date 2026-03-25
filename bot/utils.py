"""通用工具函数"""

import re


def truncate(text: str, max_len: int = 55) -> str:
    """截断字符串，超过 max_len 时在末尾加省略号。"""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "\u2026"


def parse_title_tags(title: str) -> dict:
    """从 PT 种子标题中提取技术标签。"""

    # Normalize: replace dots/underscores with spaces for easier matching
    t = title.replace(".", " ").replace("_", " ")

    # Resolution (match longer patterns first)
    resolution = ""
    for pattern in [r"\b4K\b", r"\b2160[pi]\b", r"\b1080[pi]\b", r"\b720[pi]\b", r"\b576[pi]\b", r"\b480[pi]\b"]:
        m = re.search(pattern, t, re.IGNORECASE)
        if m:
            resolution = m.group(0)
            # Normalize: "4K" stays as "4K", others keep original case
            if resolution.upper() == "4K":
                resolution = "4K"
            break

    # Source (order matters: longer/more specific first)
    source = ""
    source_patterns = [
        (r"\bBlu-?Ray\s*REMUX\b", "BluRay REMUX"),
        (r"\bREMUX\b", "REMUX"),
        (r"\bBlu-?Ray\b", "BluRay"),
        (r"\bBDRip\b", "BDRip"),
        (r"\bWEB-?DL\b", "WEB-DL"),
        (r"\bWEB-?Rip\b", "WEBRip"),
        (r"\bWEB\b", "WEB"),
        (r"\bHDTV\b", "HDTV"),
        (r"\bDVD-?Rip\b", "DVDRip"),
        (r"\bDVD\b", "DVD"),
    ]
    for pattern, label in source_patterns:
        if re.search(pattern, t, re.IGNORECASE):
            source = label
            break

    # Codec (longer patterns first)
    codec = ""
    codec_patterns = [
        (r"\bHEVC\b", "HEVC"),
        (r"\b[xX]\.?265\b", "x265"),
        (r"\bH\.?265\b", "H.265"),
        (r"\b[xX]\.?264\b", "x264"),
        (r"\bH\.?264\b", "H.264"),
        (r"\bAVC\b", "AVC"),
        (r"\bAV1\b", "AV1"),
        (r"\bVC-?1\b", "VC-1"),
        (r"\bMPEG-?2\b", "MPEG-2"),
    ]
    for pattern, label in codec_patterns:
        if re.search(pattern, t, re.IGNORECASE):
            codec = label
            break

    # Audio (longer/more specific first)
    audio = ""
    audio_patterns = [
        (r"\bDTS-?HD[\s.]?MA\b", "DTS-HD MA"),
        (r"\bDTS-?HD\b", "DTS-HD"),
        (r"\bTrueHD\s*(?:Atmos|7\.1)?\b", "TrueHD"),
        (r"\bAtmos\b", "Atmos"),
        (r"\bDTS-?X\b", "DTS-X"),
        (r"\bDTS\b", "DTS"),
        (r"\bDD\+|DDP\b", "DD+"),
        (r"\bDD5?\.?1\b", "DD"),
        (r"\bFLAC\b", "FLAC"),
        (r"\bAAC\b", "AAC"),
        (r"\bAC-?3\b", "AC3"),
        (r"\bLPCM\b", "LPCM"),
    ]
    for pattern, label in audio_patterns:
        if re.search(pattern, t, re.IGNORECASE):
            audio = label
            break

    # HDR (longer patterns first)
    hdr = ""
    hdr_patterns = [
        (r"\bDolby\s*Vision\b|\bDoVi\b", "Dolby Vision"),
        (r"\bHDR10\+\b", "HDR10+"),
        (r"\bHDR10\b", "HDR10"),
        (r"\bHDR\b", "HDR"),
        (r"\bDV\b", "DV"),
        (r"\bHLG\b", "HLG"),
        (r"\bSDR\b", "SDR"),
    ]
    for pattern, label in hdr_patterns:
        if re.search(pattern, t, re.IGNORECASE):
            hdr = label
            break

    # Clean title: remove all matched tags, release group (dash + alphanumeric at end),
    # year-like patterns stay, convert dots/underscores to spaces, collapse whitespace
    clean = title
    # Remove release group at end: -FGT, -WIKI, @FRDS etc
    clean = re.sub(r'[-@][A-Za-z0-9]+$', '', clean)
    # Replace dots and underscores with spaces
    clean = clean.replace(".", " ").replace("_", " ")
    # Remove all technical tags we found
    for pattern_list in [source_patterns, codec_patterns, audio_patterns, hdr_patterns]:
        for pattern, _ in pattern_list:
            clean = re.sub(pattern, "", clean, flags=re.IGNORECASE)
    # Remove resolution
    for pattern in [r"\b4K\b", r"\b2160[pi]\b", r"\b1080[pi]\b", r"\b720[pi]\b", r"\b576[pi]\b", r"\b480[pi]\b"]:
        clean = re.sub(pattern, "", clean, flags=re.IGNORECASE)
    # Remove common noise words
    clean = re.sub(r"\bUHD\b|\bComplete\b|\bPack\b", "", clean, flags=re.IGNORECASE)
    # Remove channel info like 5.1, 7.1, 2.0 (dots already replaced with spaces)
    clean = re.sub(r"\b[257][\s.][01]\b", "", clean)
    # Collapse whitespace and strip
    clean = re.sub(r"\s+", " ", clean).strip()

    return {
        "clean_title": clean,
        "resolution": resolution,
        "codec": codec,
        "source": source,
        "audio": audio,
        "hdr": hdr,
    }
