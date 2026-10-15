"""The code in each lesson's check-in QR, fixed here for good.

They're printed on lesson slides, so a code must never change. Keeping them in
source rather than the database means no redeploy, failover, restore from
backup or fresh database can ever make different ones. test_lesson_codes.py
pins them: changing or removing one fails CI on purpose.

Random rather than readable so a cadet can't type in the address of a lesson
they weren't at. Adding a subject or lesson means adding its codes here (the
test says which are missing); pick new ones from LESSON_CODE_ALPHABET.
"""

# Lowercase letters and digits without 0/o, 1/l/i, so a code read off a slide
# can be typed if the camera won't scan.
LESSON_CODE_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"

LESSON_CODES: dict[tuple[str, int], str] = {
    ("blue_radio", 1): "2kug967n",
    ("blue_leadership", 1): "en54ne3s",
    ("blue_first_aid", 1): "rj26efpd",
    ("moi_presentation_skills", 1): "tfv4xd2s",
    ("acp_32_2", 1): "e6a6b896",
    ("acp_32_2", 2): "zh7wz5b3",
    ("acp_32_2", 3): "3zd68cpb",
    ("acp_33_2", 1): "u3m8z2v8",
    ("acp_33_2", 2): "mzfbg2nv",
    ("acp_33_2", 3): "mhfry7yy",
    ("acp_34_2", 1): "w89g5pyg",
    ("acp_34_2", 2): "3f2gzuwt",
    ("acp_34_2", 3): "cc66qa2y",
    ("acp_32_3", 1): "8hbe2t9b",
    ("acp_32_3", 2): "yr2vvnm6",
    ("acp_32_3", 3): "hug28d5e",
    ("air_power", 1): "5f3t4h6g",
    ("air_power", 2): "kueqgm4y",
    ("air_power", 3): "dj63rpw2",
    ("acp_34_3", 1): "fjw3yeg4",
    ("acp_34_3", 2): "hn8rcxqp",
    ("acp_34_3", 3): "8jp457eu",
    ("acp_33_4", 1): "pn48fy7v",
    ("acp_33_4", 2): "nmeevrpf",
    ("acp_33_4", 3): "euzgsvmu",
    ("jet_engine_propulsion", 1): "nmg97a3v",
    ("jet_engine_propulsion", 2): "c7z5u5m4",
    ("jet_engine_propulsion", 3): "trspymb5",
    ("military_aircraft", 1): "5uzsx5vm",
    ("military_aircraft", 2): "ranenzgx",
    ("military_aircraft", 3): "ma5nksj5",
    ("acp_33_3", 1): "hb99f7q2",
    ("acp_33_3", 2): "9wctjdqz",
    ("acp_33_3", 3): "k5vnq5v4",
    ("acp_32_4", 1): "uh8w5kzf",
    ("acp_32_4", 2): "ttkr956j",
    ("acp_32_4", 3): "d4nh87vk",
    ("acp_35_3", 1): "beb94p8z",
    ("acp_35_3", 2): "zn5cr8mp",
    ("acp_35_3", 3): "ppqcde9x",
    ("rocketry", 1): "szu8xnxj",
    ("rocketry", 2): "gct3s37b",
    ("rocketry", 3): "spf8vdf8",
    ("acp_35_4", 1): "mz2u26qk",
    ("acp_35_4", 2): "8dq65mxq",
    ("acp_35_4", 3): "mfkcyhed",
}

LESSON_BY_CODE: dict[str, tuple[str, int]] = {code: lesson for lesson, code in LESSON_CODES.items()}
