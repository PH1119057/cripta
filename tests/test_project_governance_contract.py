from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


ACTIVE_PROJECT_SOURCE_FAMILIES = (
    "CHATGPT_INTERACTION_RULES_RU*.md",
    "CRIPTA_ASSISTANT_WORK_RULES_RU_*.md",
    "CRIPTA_ARCHITECTURE_RULES_RU_*.md",
    "DOCUMENTATION_INDEX_RU*.md",
    "CRIPTA_GLOSSARY_RU*.md",
    "CURRENT_PROJECT_MAP_RU*.md",
    "TRADING_CONTOUR_RU*.md",
    "OBSERVATION_ANALYTICS_RU*.md",
    "DEVELOPMENT_RELEASE_RULES_RU*.md",
    "RESEARCH_COMPUTE_RULES_RU*.md",
    "SECURITY.md",
)

LEGACY_STANDALONE_DOCS = (
    "PROJECT_GOVERNANCE_RU.md",
    "DOCUMENT_AUTHORITY_RU.md",
    "PROJECT_ARCHITECTURE_RU.md",
    "STRATEGY_ENTRY_ARCHITECTURE_RU.md",
    "SIGNAL_LIFECYCLE_CONTRACT_RU.md",
    "STRATEGY_DISPATCHER_ARCHITECTURE_RU.md",
)


def test_documentation_index_is_the_active_authority_router() -> None:
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    interaction = _read("docs/CHATGPT_INTERACTION_RULES_RU.md")
    agents = _read("AGENTS.md")
    work_rules = _read("docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md")

    assert "**Статус:** канонический индекс документации" in index
    assert "# 3. 11 файлов ChatGPT Project Source" in index
    for family in ACTIVE_PROJECT_SOURCE_FAMILIES:
        assert family in index

    assert "`AGENTS.md` — GitHub-only bootstrap" in index
    assert "GitHub `PH1119057/cripta:main`" in interaction
    assert "синхронизированное operational mirror" in interaction
    assert "HARD_STOP=YES" in interaction
    assert "OWNER_DECISION_REQUIRED=YES" in interaction

    # The old standalone documentation topology must not silently regain
    # authority merely because historical copies still exist in archive/history.
    for legacy_name in LEGACY_STANDALONE_DOCS:
        assert legacy_name not in index

    for token in ("REMOTE_HEAD", "SOURCE_HEAD", "INSTALLED_COMMIT", "LOADED_COMMIT"):
        assert token in work_rules
    assert "DEPLOY EXACT VERIFIED COMMIT" in work_rules
    assert "INDEPENDENT REMOTE SHA VERIFICATION" in work_rules

    assert "AUTHORITATIVE: GitHub PH1119057/cripta:main" in agents
    assert "OPERATIONAL MIRROR: /srv/cripta/source_checkout" in agents


def test_upper_architecture_and_supporting_contour_preserve_layer_ownership() -> None:
    architecture = _read("docs/CRIPTA_ARCHITECTURE_RULES_RU_V1.md")
    trading = _read("docs/TRADING_CONTOUR_RU.md")
    observation = _read("docs/OBSERVATION_ANALYTICS_RU.md")

    assert "Strategy layer — единственный владелец торгового смысла" in architecture
    assert "Entry Engine — универсальный активный исполнитель EntryPlan." in architecture
    assert "Exit Engine получает/claim-ит эту StrategyPosition" in architecture
    assert "Execution — техническая граница биржевой мутации." in architecture
    assert "`Risk` не является самостоятельным верхнеуровневым слоем." in architecture

    assert "Entry не вводит winner/priority/arbitration между Strategy." in trading
    assert "EXCHANGE_POSITION_OWNERSHIP_CONFLICT — штатный admission outcome" in trading

    assert "- не читает PnL Strategy как рыночный признак;" in observation
    assert "- не создаёт StrategySignal;" in observation
    assert "- не закрывает позицию по собственной оценке;" in observation


def test_root_markdown_contains_only_stable_bootstrap_entrypoints() -> None:
    assert {path.name for path in ROOT.glob("*.md")} == {"README.md", "AGENTS.md"}


def test_root_bootstrap_links_every_current_document() -> None:
    readme = _read("README.md")
    agents = _read("AGENTS.md")
    current_docs = (
        "docs/CHATGPT_INTERACTION_RULES_RU.md",
        "docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md",
        "docs/CRIPTA_ARCHITECTURE_RULES_RU_V1.md",
        "docs/DOCUMENTATION_INDEX_RU.md",
        "docs/CRIPTA_GLOSSARY_RU.md",
        "docs/CURRENT_PROJECT_MAP_RU.md",
        "docs/TRADING_CONTOUR_RU.md",
        "docs/OBSERVATION_ANALYTICS_RU.md",
        "docs/DEVELOPMENT_RELEASE_RULES_RU.md",
        "docs/RESEARCH_COMPUTE_RULES_RU.md",
        "docs/SECURITY.md",
    )
    for relative_path in current_docs:
        assert (ROOT / relative_path).is_file()
        assert relative_path in readme
        stem = relative_path.removesuffix(".md").removesuffix("_V1")
        assert stem in agents


def test_routed_process_section_numbering_is_continuous() -> None:
    import re

    for relative_path in (
        "docs/DEVELOPMENT_RELEASE_RULES_RU.md",
        "docs/RESEARCH_COMPUTE_RULES_RU.md",
    ):
        content = _read(relative_path)
        numbers = [
            int(match.group(1))
            for match in re.finditer(r"^#{1,2} (\d+)\.\s", content, flags=re.MULTILINE)
        ]
        assert numbers == list(range(1, max(numbers) + 1))


def test_security_is_mandatory_base_preread_and_all_current_docs_are_project_source() -> None:
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    work = _read("docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md")
    interaction = _read("docs/CHATGPT_INTERACTION_RULES_RU.md")
    readme = _read("README.md")
    agents = _read("AGENTS.md")

    assert "`SECURITY*.md` — обязательный technical security baseline" in interaction
    assert "7. docs/SECURITY.md" in work
    assert "7. docs/SECURITY.md" in index
    assert "7. [docs/SECURITY.md]" in readme
    assert "7. `docs/SECURITY.md`" in agents
    assert "Все 11 current docs входят в ChatGPT Project Source." in index


def test_process_state_vocabulary_has_single_glossary_authority() -> None:
    glossary = _read("docs/CRIPTA_GLOSSARY_RU.md")
    development = _read("docs/DEVELOPMENT_RELEASE_RULES_RU.md")
    research = _read("docs/RESEARCH_COMPUTE_RULES_RU.md")

    for token in ("PREPARED", "RUNNING", "COMPLETE", "FAILED", "BLOCKED"):
        assert f"**{token}**" in glossary
    assert "находятся только в\n`docs/CRIPTA_GLOSSARY_RU*.md §15`" in development
    assert "находятся в `docs/CRIPTA_GLOSSARY_RU*.md §15`" in research


def test_position_mode_mismatch_entity_and_map_fault_set_are_explicit() -> None:
    glossary = _read("docs/CRIPTA_GLOSSARY_RU.md")
    current_map = _read("docs/CURRENT_PROJECT_MAP_RU.md")

    assert "`EXCHANGE_POSITION_MODE_MISMATCH` остаётся operational/lifecycle fault." in glossary
    assert "`block_reason` не меняет его entity" in glossary
    for fault in (
        "CAPITAL_RESERVATION_STUCK",
        "EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN",
        "EXCHANGE_POSITION_MODE_MISMATCH",
        "POSITION_WITHOUT_EXIT_OWNER",
        "POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION",
    ):
        assert fault in current_map


def test_documentation_maintenance_contract_is_explicit_and_bootstrapped() -> None:
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    interaction = _read("docs/CHATGPT_INTERACTION_RULES_RU.md")
    readme = _read("README.md")

    assert "# 17. Documentation maintenance invariant" in index
    for token in (
        "Topology / bootstrap",
        "Нумерация и ссылки",
        "Один owner для повторяемых списков",
        "MAP time semantics",
        "Release/runtime identity",
        "Open architecture decisions",
        "Обязательный documentation gate",
    ):
        assert token in index
    assert "INDEX §17 documentation gate" in interaction
    assert "DOCUMENTATION_INDEX_RU.md §17" in readme


def test_server_side_scripts_require_effective_actor_permission_preflight() -> None:
    work = _read("docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md")
    development = _read("docs/DEVELOPMENT_RELEASE_RULES_RU.md")
    security = _read("docs/SECURITY.md")
    agents = _read("AGENTS.md")

    assert "Effective-actor permission preflight обязателен" in work
    for heading in (
        "## 19.1 Filesystem permission preflight выполняется ДО server-side mutation",
        "## 19.2 Проверяется право на операцию",
        "## 19.3 Systemd permission состоит из двух независимых gates",
        "## 19.4 Permission failure — preparation defect",
    ):
        assert heading in development
    assert "ReadWritePaths=" in development
    assert "operations/infrastructure/cripta-permission-preflight" in development
    assert "`chmod 777`" in security
    assert "Server-side write scripts" in agents


def test_operational_delta_identity_cannot_claim_live_identity_pass() -> None:
    glossary = _read("docs/CRIPTA_GLOSSARY_RU.md")
    development = _read("docs/DEVELOPMENT_RELEASE_RULES_RU.md")
    current_map = _read("docs/CURRENT_PROJECT_MAP_RU.md")

    assert "**OPERATIONAL_DELTA_COMMIT**" in glossary
    assert "## 26.1 Operational delta не маскируется под full release" in development
    for token in (
        "REMOTE_HEAD",
        "SOURCE_HEAD",
        "INSTALLED_COMMIT",
        "LOADED_COMMIT",
        "OPERATIONAL_DELTA_COMMIT",
    ):
        assert token in development
        assert token in current_map
    assert (
        "SOURCE_LIVE_IDENTITY                  = NOT READY FOR ARM "
        "(operational deltas outside full release)"
    ) in current_map
    assert "# 18. LIVE / MICRO_LIVE readiness — CHECKED HERE 2026-09-28" in current_map


def test_documentation_index_section_numbering_is_continuous() -> None:
    import re

    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    numbers = [
        int(match.group(1))
        for match in re.finditer(r"^# (\d+)\.\s", index, flags=re.MULTILINE)
    ]
    assert numbers == list(range(1, max(numbers) + 1))



def test_current_server_profile_is_explicit_and_routed() -> None:
    work = _read("docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md")
    current_map = _read("docs/CURRENT_PROJECT_MAP_RU.md")
    development = _read("docs/DEVELOPMENT_RELEASE_RULES_RU.md")
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    readme = _read("README.md")
    agents = _read("AGENTS.md")

    assert "## 1.4 Current server execution profile — CHECKED HERE 2026-09-28" in current_map
    for actor in ("root", "cripta", "postgres", "sentinelx"):
        assert actor in current_map
    for path_token in (
        "/srv/cripta/source_checkout",
        "/data/cripta/research_runs",
        "/data/cripta/jobs",
        "/srv/cripta-share/reports",
    ):
        assert path_token in current_map

    assert "## 5.1 Server-side script authoring начинается с actor/path contract" in development
    assert "CURRENT_PROJECT_MAP_RU*.md §1.4" in development
    assert "## 17.8 Current server operational profile" in index
    assert "CURRENT_PROJECT_MAP_RU*.md §1.4" in work
    assert "CURRENT_PROJECT_MAP_RU.md §1.4" in readme
    assert "CURRENT_PROJECT_MAP_RU*.md §1.4" in agents


def test_server_profile_keeps_sensitive_transport_out() -> None:
    current_map = _read("docs/CURRENT_PROJECT_MAP_RU.md")
    security = _read("docs/SECURITY.md")

    assert "Credential/key/SSH details в этот snapshot не входят." in current_map
    assert "credentials/key/SSH transport details" in security



def test_chatgpt_ui_capacity_limits_are_canonical_and_within_budget() -> None:
    interaction = _read("docs/CHATGPT_INTERACTION_RULES_RU.md")
    work = _read("docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md")
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    readme = _read("README.md")
    agents = _read("AGENTS.md")

    assert len(ACTIVE_PROJECT_SOURCE_FAMILIES) == 11
    assert len(ACTIVE_PROJECT_SOURCE_FAMILIES) <= 12

    for body in (interaction, work, index, readme, agents):
        assert "8000" in body
        assert "12" in body

    assert "PROJECT_INSTRUCTIONS_MAX_CHARS = 8000" in index
    assert "PROJECT_SOURCE_MAX_FILES       = 12" in index
    assert "CURRENT_PROJECT_SOURCE_FILES   = 11" in index
    assert "добавление 12-го current file требует отдельного OWNER DECISION" in index
    assert "13-й current file = HARD STOP" in index


def test_project_source_split_is_forbidden_when_it_consumes_ui_capacity() -> None:
    interaction = _read("docs/CHATGPT_INTERACTION_RULES_RU.md")
    work = _read("docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md")
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")

    assert "Нельзя дробить current\ncanonical document" in interaction
    assert "Нельзя решать рост документации простым split current docs." in work
    assert "split current document только ради размера/удобства запрещён" in index


def test_server_side_writer_routing_is_cross_route_and_data_forensic_is_explicit() -> None:
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    work = _read("docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md")
    research = _read("docs/RESEARCH_COMPUTE_RULES_RU.md")
    readme = _read("README.md")
    agents = _read("AGENTS.md")

    assert "long compute / data forensic" in index
    assert "независимо от primary route" in index
    assert "DEVELOPMENT_RELEASE §5.1 + §19.1–19.4" in work
    assert "Research route не отменяет server-side permission/actor contract." in research
    assert "data forensic / compute" in readme
    assert "server-side writer/effective-actor change" in agents


def test_version_controlled_project_instructions_template_is_within_ui_limits() -> None:
    template_path = ROOT / "operations" / "bootstrap" / "CHATGPT_PROJECT_INSTRUCTIONS_RU.txt"
    assert template_path.is_file()
    template = template_path.read_text(encoding="utf-8")

    # Check both normal LF storage and a conservative CRLF representation.
    assert len(template) <= 8000
    assert len(template.replace("\n", "\r\n")) <= 8000

    for marker in (
        "Project Instructions: не более 8000 символов",
        "Project Source: не более 12 файлов",
        "current Project Source bundle: 11 файлов",
        "data forensic -> `docs/RESEARCH_COMPUTE_RULES_RU*.md`",
        "DEVELOPMENT_RELEASE §5.1 + §19.1–19.4",
        "CURRENT_PROJECT_MAP §1.4",
    ):
        assert marker in template

    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    assert "operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt" in index
    assert "derived UI bootstrap artifact" in index
    assert len(ACTIVE_PROJECT_SOURCE_FAMILIES) == 11


def test_source_runtime_research_filesystem_contours_are_canonical() -> None:
    work = _read("docs/CRIPTA_ASSISTANT_WORK_RULES_RU_V1.md")
    glossary = _read("docs/CRIPTA_GLOSSARY_RU.md")
    development = _read("docs/DEVELOPMENT_RELEASE_RULES_RU.md")
    research = _read("docs/RESEARCH_COMPUTE_RULES_RU.md")
    current_map = _read("docs/CURRENT_PROJECT_MAP_RU.md")
    index = _read("docs/DOCUMENTATION_INDEX_RU.md")
    readme = _read("README.md")
    agents = _read("AGENTS.md")
    template = _read("operations/bootstrap/CHATGPT_PROJECT_INSTRUCTIONS_RU.txt")

    roots = (
        "/srv/cripta/source_checkout",
        "/srv/cripta/runtime",
        "/data/cripta/research",
        "/data/cripta/script_archive",
    )
    for token in roots:
        assert token in work
        assert token in development
        assert token in current_map
        assert token in index
        assert token in readme
        assert token in agents

    assert "**SOURCE contour / SOURCE_ROOT**" in glossary
    assert "**RUNTIME contour / RUNTIME_CODE_ROOT**" in glossary
    assert "**RESEARCH contour / RESEARCH_ROOT**" in glossary
    assert "**Research source snapshot**" in glossary
    assert "## 42. SOURCE / RUNTIME / RESEARCH filesystem contours" in development
    assert "## 17. Research filesystem contour" in research
    assert "SOURCE_REPRODUCIBILITY=PASS" in research
    assert "runtime не зависит напрямую от research artifacts" in template
    assert "FILESYSTEM_CONTOUR_SEPARATION" in index


def test_research_run_requires_source_capture_and_data_disk_output() -> None:
    research = _read("docs/RESEARCH_COMPUTE_RULES_RU.md")
    current_map = _read("docs/CURRENT_PROJECT_MAP_RU.md")

    for token in (
        "run_manifest.json",
        "source_commit.txt",
        "source_snapshot/",
        "source_sha256.txt",
        "input_provenance.json",
    ):
        assert token in research

    assert "все новые server-side research writes живут на" in research
    assert "/data/cripta/research_runs" in research
    assert "after PHASE R2 no new research output is allowed" in current_map
