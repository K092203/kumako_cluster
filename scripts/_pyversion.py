"""Pythonバージョン検査。各スクリプトが import するだけで発動する。

学校PCのPythonが古い場合、str.removeprefix (3.9+) 等で実行中に不可解に
落ちる代わりに、起動時に明確なメッセージで止める。このファイル自体は
Python 3.0 でも構文エラーにならない書き方に留めること。
"""

import sys

MINIMUM = (3, 9)

if sys.version_info < MINIMUM:
    raise SystemExit(
        "kumako_cluster requires Python %d.%d+ but found %s (%s). "
        "Install a newer Python or adjust PATH."
        % (MINIMUM[0], MINIMUM[1], sys.version.split()[0], sys.executable)
    )
