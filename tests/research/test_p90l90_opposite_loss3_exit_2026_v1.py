"""Research #690 independently isolated native Exit test subprocess.

The broad suite imports other test fixtures that may transiently mock the
legacy scripts.research namespace: isolate causal original modules from it.
"""
import subprocess
import sys
from pathlib import Path

CASES = (
    "from decimal import Decimal as D\nfrom scripts"
    ".research import p90l90_exit_90d_v2 as base\nf"
    "rom scripts.research import p90l90_opposite_l"
    "oss3_exit_2026_v1 as experiment\ndef event(sid"
    "e, price, ident):\n    return {\n        \"symbo"
    "l\": \"LTCUSDT\", \"side\": side, \"price\": str(pri"
    "ce),\n        \"ts\": \"100\", \"event_id\": ident, "
    "\"trade_id\": ident,\n        \"PREV7D\": True,\n  "
    "  }\n\n\ndef source_position(side, price):\n    r"
    "eturn base.initial_position(\n        event(si"
    "de, price, \"opp-\" + side), 1, 0, \"PREV7D\", \"T"
    "RAIL035\"\n    )\n\n\ndef open_new(side, price, op"
    "posite, cap=\"OPP3\"):\n    book = experiment.Ti"
    "meBook(cap, \"ALL\")\n    book.handle(\n        \""
    "LTCUSDT\", 100, 50, D(str(price)),\n        (ev"
    "ent(side, price, \"new-\" + side),),\n        (D"
    "(\"90\"), D(\"110\")), opponent=opposite,\n    )\n "
    "   assert book.count[\"accepted\"] == 1\n    ret"
    "urn book, book.pos[\"LTCUSDT\"]\n\n\ndef test_exac"
    "t_loss_three_short_opponent_long_new():\n    o"
    "pposite = source_position(\"SHORT\", \"100\")\n   "
    " opposite.stop = D(\"101\")  # tightened live s"
    "top must NOT replace original -10%\n    book, "
    "pos = open_new(\"LONG\", \"103\", opposite)\n    a"
    "ssert pos._r690_special is True\n    assert po"
    "s.tp == D(\"110\")\n    assert pos.tp_live_after"
    "_seq == 50\n    assert book.special_entries[0]"
    "[\"opponent_unrealized_gross_pct\"] == \"-3.00\"\n"
    "    assert book.special_entries[0][\"target_pr"
    "ice\"] == \"110.0\"\n\n\ndef test_exact_loss_three_"
    "long_opponent_short_new():\n    opposite = sou"
    "rce_position(\"LONG\", \"100\")\n    opposite.stop"
    " = D(\"99\")\n    book, pos = open_new(\"SHORT\", "
    "\"97\", opposite)\n    assert pos._r690_special "
    "is True\n    assert pos.tp == D(\"90\")\n    asse"
    "rt len(book.special_entries) == 1\n\n\ndef test_"
    "loss_2point99_does_not_trigger():\n    for opp"
    "osite_side, new_side, price in (\n        (\"SH"
    "ORT\", \"LONG\", \"102.99\"),\n        (\"LONG\", \"SH"
    "ORT\", \"97.01\"),\n    ):\n        book, pos = op"
    "en_new(\n            new_side, price, source_p"
    "osition(opposite_side, \"100\")\n        )\n     "
    "   assert not getattr(pos, \"_r690_special\", F"
    "alse)\n        assert not book.special_entries"
    "\n\n\ndef test_no_opponent_no_trigger():\n    boo"
    "k, pos = open_new(\"LONG\", \"103\", None)\n    as"
    "sert not getattr(pos, \"_r690_special\", False)"
    "\n    assert book.special_entries == []\n\n\ndef "
    "test_special_trail_activates_only_after_1poin"
    "t2():\n    _, pos = open_new(\"LONG\", \"103\", so"
    "urce_position(\"SHORT\", \"100\"))\n    initial_st"
    "op = pos.stop\n    returned, outcome = experim"
    "ent.special_on_trade(\n        pos, D(\"104.225"
    "\"), 51, 0, D(\"200\"), \"TRAIL035\"\n    )\n    ass"
    "ert returned is pos and outcome is None\n    a"
    "ssert pos.stop == initial_stop\n    returned, "
    "outcome = experiment.special_on_trade(\n      "
    "  pos, D(\"104.236\"), 52, 0, D(\"200\"), \"TRAIL0"
    "35\"\n    )\n    assert returned is pos and outc"
    "ome is None\n    assert pos.protected and pos."
    "stop == D(\"103.206\")\n    returned, outcome = "
    "experiment.special_on_trade(\n        pos, pos"
    ".stop, 53, 0, D(\"200\"), \"TRAIL035\"\n    )\n    "
    "assert returned is None and outcome[0] == \"PR"
    "OTECTED_STOP\"\n\n\ndef test_fixed_target_resting"
    "_cannot_hit_on_entry_tick():\n    _, pos = ope"
    "n_new(\"LONG\", \"103\", source_position(\"SHORT\","
    " \"100\"))\n    returned, outcome = experiment.s"
    "pecial_on_trade(\n        pos, D(\"110\"), 50, 0"
    ", D(\"1\"), \"TRAIL035\"\n    )\n    assert returne"
    "d is pos and outcome is None\n    returned, ou"
    "tcome = experiment.special_on_trade(\n        "
    "pos, D(\"110\"), 51, 0, D(\"1\"), \"TRAIL035\"\n    "
    ")\n    assert returned is None\n    assert outc"
    "ome == (\"RESTING_TP\", D(\"110\"), True)\n\n\ndef t"
    "est_control_remains_unmodified():\n    opposit"
    "e = source_position(\"SHORT\", \"100\")\n    book,"
    " pos = open_new(\"LONG\", \"103\", opposite, cap="
    "None)\n    assert not getattr(pos, \"_r690_spec"
    "ial\", False)\n    assert not book.special_entr"
    "ies\n    assert pos.tp == D(\"110\")\n\nif __name_"
    "_ == \"__main__\":\n    for name, func in sorted"
    "(globals().copy().items()):\n        if name.s"
    "tartswith(\"test_\") and callable(func):\n      "
    "      func()\n            print(\"PASS\", name)\n"
)


def test_exact_opponent_loss3_policy_in_separate_process():
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-B", "-c", CASES],
        cwd=repo, capture_output=True, text=True, timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS test_") == 7, result.stdout
