"""Which basketball this process is building: the men's or the women's.

The two sections share every line of code that is about basketball (the
rules, the simulator, the pages) and differ only in where their data comes
from and where they are published. That difference lives here, chosen once
per process from B12_BB, which bb_build.py sets from --women before anything
else is imported:

  men     /basketball/            CollegeBasketballData.com, ESPN for fast
                                  finals, the-odds-api basketball_ncaab
  women   /womens-basketball/     ESPN for everything (CBBD has no women's
                                  data), the-odds-api basketball_wncaab

The men's paths are exactly what they were before the women's section
existed, so nothing already published moves.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
WOMEN = os.environ.get("B12_BB") == "women"

if WOMEN:
    LABEL = "Women’s Basketball"
    NOUN = "women's basketball"
    PATH = "womens-basketball"
    SECTION, SCHED, ATT = ("wbasketball", "wbasketball-schedule",
                           "wbasketball-attendance")
    DATA = os.path.join(HERE, "data", "women")
    SITE = os.path.join(HERE, "site_women")
    LEAGUE = "womens-college-basketball"
    ODDS_SPORT = "basketball_wncaab"
    SOURCE = "ESPN"
else:
    LABEL = "Men’s Basketball"
    NOUN = "men's basketball"
    PATH = "basketball"
    SECTION, SCHED, ATT = ("basketball", "basketball-schedule",
                           "basketball-attendance")
    DATA = os.path.join(HERE, "data")
    SITE = os.path.join(HERE, "site")
    LEAGUE = "mens-college-basketball"
    ODDS_SPORT = "basketball_ncaab"
    SOURCE = "CollegeBasketballData"

URL = f"https://big12ology.com/{PATH}/"
RAW = os.path.join(DATA, "raw")
