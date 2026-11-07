Zone files of the IANA tz database (release 2026e), for the tests of `core/tzif.py` and `core/localtime.py`.

Built from `tzdata.zi` with `zic -b slim` (`.slim`: transitions only up to the last rule change, the POSIX TZ string
at the end of the file does the rest) and `zic -b fat` (`.fat`: transitions listed up to 2037).
