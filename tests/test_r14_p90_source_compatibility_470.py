"""R14 regression for exact R06-origin P90 source compatibility.

The #470/#642 release must recognize only the accepted R06 financial promotion,
R07 provenance and R10 PG-publication consumer bytes when the active real source
epoch still names the physically accepted R06 origin. Historical attestations
remain bound to their own exact Git-verified consumer digests.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from bybit_workbench.universal_entry import p90l90_real_source_compatibility as compat


def test_r14_r06_origin_accepts_only_exact_reviewed_four_file_delta() -> None:
    expected = compat._REVIEWED_R07_R10_PUBLIC_SOURCE_SHA256
    assert set(expected) == {
        "universal_entry/engine.py",
        "universal_entry/p90l90_real_contract_v3.py",
        "universal_entry/r06_financial_promotion.py",
        "universal_entry/r07_publication_provenance.py",
    }
    changed = set(expected)

    assert compat._reviewed_delta_for_origin(
        changed,
        origin_commit=compat._REVIEWED_R06_SOURCE_ORIGIN,
    ) == expected

    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="UNREVIEWED_CONSUMER_DELTA",
    ):
        compat._reviewed_delta_for_origin(
            changed | {"universal_exit/unreviewed.py"},
            origin_commit=compat._REVIEWED_R06_SOURCE_ORIGIN,
        )

    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="UNREVIEWED_CONSUMER_DELTA",
    ):
        compat._reviewed_delta_for_origin(
            changed,
            origin_commit="0" * 40,
        )


def test_r14_854_reviewed_p90_consumer_hashes_bind_current_exact_git_bytes() -> None:
    # Historical #470/#820/#824 hash vectors must stay frozen. The sole
    # changed consumer in #854 is the reviewed P90 protection check.
    old = compat._REVIEWED_R06_ACCOUNT_LOCK_ORDER_SHA256
    reviewed = compat._REVIEWED_R06_ACCOUNT_LOCK_ORDER_R10_854_SHA256
    path = "universal_entry/p90l90_real_contract_v3.py"
    assert old[path] == "2bdd80448ae7cc6274078939f8b41ebd706b09e089824ec69c99052416395ac7"
    assert reviewed[path] == "eeee1198827b8c42f233c9dd0faef66febbf97cffb1d882a173d54a7bb30ab21"
    assert set(old) == set(reviewed)
    assert all(
        old[key] == new_sha for key, new_sha in reviewed.items() if key != path
    )
    for relative, expected in reviewed.items():
        blob = subprocess.check_output([
            "git", "show", f"HEAD:src/bybit_workbench/{relative}",
        ])
        assert hashlib.sha256(blob).hexdigest() == expected

    assert compat._reviewed_delta_for_origin(
        set(reviewed),
        origin_commit=compat._REVIEWED_R06_SOURCE_ORIGIN,
        current_hashes=reviewed,
    ) == reviewed
    # One unreviewed extra file or a single altered byte must never inherit
    # the #854 source-origin compatibility exemption.
    with pytest.raises(
        compat.P90RealSourceCompatibilityUnverified,
        match="UNREVIEWED_CONSUMER_DELTA",
    ):
        compat._reviewed_delta_for_origin(
            set(reviewed) | {"universal_entry/unreviewed.py"},
            origin_commit=compat._REVIEWED_R06_SOURCE_ORIGIN,
            current_hashes=reviewed,
        )
    changed = dict(reviewed)
    changed[path] = "0" * 64
    assert compat._reviewed_delta_for_origin(
        set(reviewed),
        origin_commit=compat._REVIEWED_R06_SOURCE_ORIGIN,
        current_hashes=changed,
    ) == old


def test_r14_r06_origin_accepts_exact_account_lock_order_delta() -> None:
    reviewed = compat._REVIEWED_R06_ACCOUNT_LOCK_ORDER_SHA256
    assert set(reviewed) == {
        "universal_entry/engine.py",
        "universal_entry/p90l90_real_contract_v3.py",
        "universal_entry/r06_financial_promotion.py",
        "universal_entry/r07_publication_provenance.py",
        "universal_exit/p90l90_real_closed_exit_port.py",
        "universal_exit/p90l90_real_v3.py",
        "universal_exit/p90l90_real_exit_execution.py",
    }
    assert reviewed["universal_exit/p90l90_real_exit_execution.py"] == (
        "a31cd9c03de6187483b6eb78598b12faeb258ac55221da625d00caec0b3c3358"
    )
    assert compat._reviewed_delta_for_origin(
        set(reviewed),
        origin_commit=compat._REVIEWED_R06_SOURCE_ORIGIN,
    ) == reviewed


def test_r14_r06_origin_accepts_exact_protected_exit_recovery_delta() -> None:
    reviewed = compat._REVIEWED_R06_PROTECTED_EXIT_RECOVERY_SHA256
    assert set(reviewed) == {
        "universal_entry/engine.py",
        "universal_entry/p90l90_real_contract_v3.py",
        "universal_entry/r06_financial_promotion.py",
        "universal_entry/r07_publication_provenance.py",
        "universal_exit/p90l90_real_closed_exit_port.py",
        "universal_exit/p90l90_real_v3.py",
    }
    assert compat._reviewed_delta_for_origin(
        set(reviewed),
        origin_commit=compat._REVIEWED_R06_SOURCE_ORIGIN,
    ) == reviewed
    # This is a preserved historical SHA vector, not a statement about
    # the new HEAD after the explicitly reviewed #854 P90 consumer change.
    assert reviewed["universal_entry/p90l90_real_contract_v3.py"] == (
        "2bdd80448ae7cc6274078939f8b41ebd706b09e089824ec69c99052416395ac7"
    )


_R06_PRE_PROTECTED_EXIT_RECOVERY_RELEASES = (
    "ceff1305195691e3db4d582431a8184947365567",
    "22af6687f2317756cc29c8563cd482b54a925e18",
    "41a707f2e936474735c1f3babec3018d574268bc",
    "7b12a55fd62eb9fd2e935f87cdebf066d303e98f",
    "a13b782b95f4bcfeb169b1a8795a06cd224b8f40",
    "b70b1fd05f955ce273538f6a236d842c818913ce",
    "c4ae9d80ed97260a8e0072c1c20e8f61d6443728",
    "fe03b8d247ae0186b224b1c65e09091150b8049c",
    "11e2ebb3bb87dc2aa79ac2f0a650250c340d5b5c",
    "23bee96f1d55eb2f1fd380f5eb6f30fe40ee5e88",
    "ee77b61802df0c59410e21da697c23718a2159b8",
    "5eec79cf608d226870534210a06046039bdcc96a",
    "49406c5e9d94a6c2028338fb3a48c7d2d300492b",
    "cdbec0afba756c88a5553e16c116401eecd3b4ba",
    "c97a04cc158e0b3456970ae99283e3a6aa8a3812",
)
_R06_PRE_PROTECTED_EXIT_RECOVERY_DIGEST = (
    "d9a9cc3f46b1f469fe1d4033d2f1b1d757baa2a5067dda773f0595a5d05b0604"
)


@pytest.mark.parametrize("release", _R06_PRE_PROTECTED_EXIT_RECOVERY_RELEASES)
def test_r14_pre_protected_exit_rows_keep_exact_historical_digest(
    release: str,
) -> None:
    assert compat._REVIEWED_HISTORICAL_CONSUMERS[release] == (
        compat._R06_PRE_PROTECTED_EXIT_RECOVERY_HISTORICAL_DIGEST
    ) == _R06_PRE_PROTECTED_EXIT_RECOVERY_DIGEST


@pytest.mark.parametrize("release", _R06_PRE_PROTECTED_EXIT_RECOVERY_RELEASES)
def test_r14_historical_d9a9_digest_recomputes_from_git_when_object_available(
    release: str,
) -> None:
    repo = Path.cwd()
    if not (repo / ".git").is_dir():
        worktrees = subprocess.check_output(
            ["git", "worktree", "list", "--porcelain"],
            text=True,
        ).splitlines()
        roots = [
            Path(line.removeprefix("worktree "))
            for line in worktrees
            if line.startswith("worktree ")
        ]
        repo = next(
            (root for root in roots if (root / ".git").is_dir()),
            repo,
        )
    if not (repo / ".git").is_dir():
        pytest.skip("full Git checkout unavailable")
    probe = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{release}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("historical Git object unavailable in this checkout")
    inventory = compat._consumer_inventory_from_git(repo, release)
    digest = hashlib.sha256(compat.CONTRACT_VERSION.encode())
    for relative in sorted(inventory):
        digest.update(relative.encode() + b"\0")
        digest.update(hashlib.sha256(inventory[relative]).digest())
    assert digest.hexdigest() == _R06_PRE_PROTECTED_EXIT_RECOVERY_DIGEST


_R06_PRE_PROMOTION_RELEASES = (
    "1abfd16a10a36128c5da74b2a05844fcf9f7bc62",
    "2f27a371e97a206b0c430e4ddca3d51fd6980bb9",
    "3623ce4f528f01187ffe58285e528ec556e3f613",
    "46c6419e0ea08a6d4ab8895966f079748aaf31f3",
    "52cae12a1f74d58b55c9d681744ab9d572f69f55",
    "5e60bb622fe01db6efe1406399f86b765feabfe5",
    "68edf160aff1b65d9d37e93bc52446f11ebc5afc",
    "72f39e3aa139862842900a9fb9e3cb39911aec7c",
    "759c419dd42dc6b0f048d0bfd917d15f9da4dd18",
    "796a10ab62df1c21dbe4bd72ef4dcbb8cc1b0ee6",
    "7eaf2d71673676ed28f336d0bd8b8d78a921169b",
    "8ff4d34da2f91bdac026488a2d360aabcd7251ba",
    "988fc1ae6aca50acd4f122bbd06856f8c695df2e",
    "bfe40a81ab90b936e61844c337ab966612459a9f",
    "ce212eaef4157bd06afdd5b489e6792269082e4d",
    "dcedda4dd9e47a1ebaa5797f089ce5569f8177a8",
    "f61af319084f4d5bf50ec305ae54aa3ba8391be4",
)


@pytest.mark.parametrize("release", _R06_PRE_PROMOTION_RELEASES)
def test_r14_pre_promotion_attestations_keep_exact_historical_digest(
    release: str,
) -> None:
    assert (
        compat._REVIEWED_HISTORICAL_CONSUMERS[release]
        == compat._R06_PRE_PROMOTION_HISTORICAL_DIGEST
        == "f492f4449816dd9ed4d36b377d3fbc0679c0e007a816c4225e39fbb2ca0843c8"
    )


def test_r14_unlisted_release_does_not_inherit_pre_promotion_digest() -> None:
    assert "0" * 40 not in compat._REVIEWED_HISTORICAL_CONSUMERS


_R06_POST_PROMOTION_RELEASES = (
    "4258d34c16b37c33c63016f39d79002e386aee4e",
    "4f2dca7d2820a4f0474ef32d7135396d35f5153f",
    "5f841e3709b72c9e5a82892570bd36d63296222f",
    "9b0ec2f589787babe88e90bccec661a2a648558f",
    "de7dc6b8646f8f2be1d9a4cd83e1752230e57e27",
    "e88f8ceb602f28c0e220398c66ead856da83b778",
    "f68f0e42d389d626a707495e3e39c4625d434676",
    "f77a074aaf2982dfed881a0efcc80567d449d336",
)



@pytest.mark.parametrize("release", _R06_POST_PROMOTION_RELEASES)
def test_r14_post_promotion_attestations_keep_exact_historical_digest(
    release: str,
) -> None:
    expected = compat._R06_POST_PROMOTION_HISTORICAL_DIGEST
    assert (
        compat._REVIEWED_HISTORICAL_CONSUMERS[release]
        == expected
        == "38b45713f811d96cc8bf1ed48cd98a5f26b4fa3fc26d33a1b6309fc2278e437e"
    )


def test_r14_unlisted_release_does_not_inherit_post_promotion_digest() -> None:
    assert "f" * 40 not in compat._REVIEWED_HISTORICAL_CONSUMERS


# #854 release-preparation regression: the older active R06 source epoch
# already contains these exact append-only release attestations. Both values
# were reconstructed independently from 99 consumer Git blobs for EACH SHA.
_R06_PRE_R10_HISTORICAL_RELEASES = (
    "21385b16898190d8a222b0ee3fa01a48ea514d5f",
    "46cf79ac2ac53872548c8d5e960bacded877a98a",
    "549ba6863556e47adcfab71cee8753b941455b5f",
    "643410ccbf8b97d1a564c95fe3c64eebbd67cdb6",
    "66c35fb0177a929573776b9349a0f28fc318778b",
    "6b49ba6ef6bf3c3041c8b0f5ddee9f4879cd5acd",
    "6feadeef3f3308b7ed45a6d0202105007568f5a5",
    "7608434c84a2bf5546be75bf682e3d212b5f5865",
    "e25e455038aed397507818f0508b9bbfbabc3568",
    "e9d71bfb0729c642707168b74d97c8640f0bb8bf",
)
_R06_PRE_R10_HISTORICAL_DIGEST = (
    "173df8aab8a71bf329c560e010f068618f3abb0ba8722018edd321349211b6a8"
)


def test_r14_r10_release_historical_attestations_exact_allowlist() -> None:
    assert len(_R06_PRE_R10_HISTORICAL_RELEASES) == 10
    assert len(set(_R06_PRE_R10_HISTORICAL_RELEASES)) == 10
    assert compat._R06_20261118_PRE_R10_HISTORICAL_DIGEST == (
        _R06_PRE_R10_HISTORICAL_DIGEST
    )
    assert all(
        compat._REVIEWED_HISTORICAL_CONSUMERS.get(release)
        == _R06_PRE_R10_HISTORICAL_DIGEST
        for release in _R06_PRE_R10_HISTORICAL_RELEASES
    )
    # No lineage-prefix or arbitrary same-looking new release is admitted.
    assert compat._REVIEWED_HISTORICAL_CONSUMERS.get("0" * 40) is None
    assert compat._REVIEWED_HISTORICAL_CONSUMERS.get(
        "6c4027ca77811df3bafd42516df1cbd05f5c385b"
    ) is None


@pytest.mark.parametrize("release", _R06_PRE_R10_HISTORICAL_RELEASES)
def test_r14_r10_historical_99_consumer_git_objects_exact(
    release: str,
) -> None:
    root = Path.cwd()
    if not (root / ".git").is_dir():
        pytest.skip("full Git checkout unavailable")
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{release}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("exact historical commit missing in checkout")
    inventory = compat._consumer_inventory_from_git(root, release)
    assert len(inventory) == 99
    digest = hashlib.sha256(compat.CONTRACT_VERSION.encode())
    for relative in sorted(inventory):
        digest.update(relative.encode() + b"\0")
        digest.update(hashlib.sha256(inventory[relative]).digest())
    assert digest.hexdigest() == _R06_PRE_R10_HISTORICAL_DIGEST
