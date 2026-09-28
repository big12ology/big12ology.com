#!/usr/bin/env python3
"""What the chance of precipitation is a chance of, and whether it thunders.

    python3 tests/test_weather_kind.py

Open-Meteo's probability covers anything that falls, so the page used to
call every percentage rain. weather.py now names it from three models at
once: rain or snow by which brings more water, and thunder by a majority of
the models' weather codes. Both rules are easy to get subtly wrong: snowfall
is a depth in centimeters while rain is water in millimeters, and a vote
over models has to shrink when ICON runs out past a week.

No network. Hand-built hourly blocks in the shape the API returns for a
multi-model request, each field suffixed with the model's name.
"""
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import weather                                           # noqa: E402

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


WHEN = datetime.datetime(2026, 11, 21, 23, tzinfo=datetime.timezone.utc)
STAMP = "2026-11-21T23:00"
GFS, EC, ICON = weather.MODELS


def hour(temp=40, pop=60, rain=(0, 0, 0), snow=(0, 0, 0), codes=(3, 3, 3)):
    """One hour from all three models. None in a slot means that model has
    no answer, which is what ICON looks like past its horizon."""
    h = {"time": [STAMP]}
    for field, vals in (("temperature_2m", (temp,) * 3),
                        ("wind_speed_10m", (5, 5, 5)),
                        ("precipitation_probability", (pop,) * 3),
                        ("rain", rain), ("showers", (0, 0, 0)),
                        ("snowfall", snow), ("weather_code", codes)):
        for m, v in zip((GFS, EC, ICON), vals):
            h[f"{field}_{m}"] = [v]
    return h


w = weather._at_hour(hour(rain=(1.0, 0.8, 1.2)), WHEN)
check(w["precipType"] == "rain", "a wet 40F hour was not rain")
check("thunder" not in w, "an overcast hour was called a storm")

w = weather._at_hour(hour(temp=28, snow=(1.0, 1.4, 0.9)), WHEN)
check(w["precipType"] == "snow", "a snowy 28F hour was not snow")

# The units trap. 0.5 cm of snow is about 0.7 mm of water, which beats
# 0.6 mm of rain. Compared raw, 0.5 against 0.6, rain would win.
w = weather._at_hour(hour(temp=33, rain=(0.6, 0.6, 0.6),
                          snow=(0.5, 0.5, 0.5)), WHEN)
check(w["precipType"] == "snow",
      "snow depth was compared to rain water without converting")

# A chance with no modeled amount falls back to the thermometer.
check(weather._at_hour(hour(temp=30, pop=15), WHEN)["precipType"] == "snow",
      "a dry 30F hour with a chance did not fall back to snow")
check(weather._at_hour(hour(temp=50, pop=15), WHEN)["precipType"] == "rain",
      "a dry 50F hour with a chance did not fall back to rain")

# Thunder takes a majority, not any one model.
check(weather._at_hour(hour(codes=(95, 95, 80)), WHEN).get("thunder"),
      "two of three models on a thunderstorm was not a storm")
check(not weather._at_hour(hour(codes=(95, 80, 80)), WHEN).get("thunder"),
      "one model alone put a storm on the card")
# Past ICON's horizon the vote is over two, and it takes both.
check(not weather._at_hour(hour(codes=(95, 80, None)), WHEN).get("thunder"),
      "one of two models put a storm on the card")
check(weather._at_hour(hour(codes=(95, 96, None)), WHEN).get("thunder"),
      "both remaining models on a storm was not a storm")

# The average itself, over only the models that answered.
w = weather._at_hour(hour(), WHEN)
check(w["tempF"] == 40 and w["precipChance"] == 60, "averages drifted")
h = hour()
h[f"temperature_2m_{ICON}"] = [None]
h[f"temperature_2m_{EC}"] = [50]
check(weather._at_hour(h, WHEN)["tempF"] == 45,
      "a missing model was counted as zero instead of left out")

if FAIL:
    print("weather kind: FAILED")
    for m in FAIL:
        print("  FAIL:", m)
    sys.exit(1)
print("weather kind: rain and snow compared as water, a dry chance falls "
      "back to the thermometer, and thunder takes a majority of the models "
      "that answered")
