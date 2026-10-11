
import hashlib
import re

TEMPLATE_TYPE = "github_pr_merged_to_slack"
SUPPORTED_TRIGGER = "GITHUB_PULL_REQUEST_MERGED"
SLACK_ACTION = "SLACK_SEND_MESSAGE"
ALLOWED_TOOLS = frozenset({"github", "slack"})
RESOLUTION_KIND = "destination_resolution"
RESOLUTION_VERSION = 1
DESTINATION_SOURCE = "automation_config"

REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$"
)
CHANNEL_ID_PATTERN = re.compile(r"^[CG][A-Z0-9]{8,}$")
PROPOSAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
SAFE_TOOL_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
SLACK_WORD = re.compile(r"\bslack\b", re.IGNORECASE)
OTHER_SERVICES = re.compile(
    r"\b(jira|trello|asana|linear|notion|teams|discord|email|e-mail|gmail|outlook|"
    r"sms|whatsapp|telegram|webhook|zapier|confluence|clickup|monday)\b",
    re.IGNORECASE,
)

NOT_SLACK = "proposal does not explicitly name Slack as the action; generic notifications are not converted"
OTHER_TOOLS = "proposal uses tools other than GitHub and Slack; only Slack notifications are supported"
OTHER_SERVICE = "proposal mentions services other than Slack; it cannot be reduced to a Slack notification"


def normalize_trigger(trigger) -> str:
    """'GITHUB_PULL_REQUEST_MERGED', 'github.pull_request.merged', ... -> upper snake."""
    if not isinstance(trigger, str):
        return ""
    return re.sub(r"[^A-Z0-9]+", "_", trigger.strip().upper()).strip("_")


def step_list(proposal: dict) -> list[dict]:
    """Steps may be plain strings or {step_number, action, tool} objects."""
    steps = []
    for step in proposal.get("steps") or []:
        if isinstance(step, dict):
            action = step.get("action") or step.get("description") or ""
            tool = step.get("tool") or ""
            steps.append({
                "action": action if isinstance(action, str) else "",
                "tool": tool if isinstance(tool, str) else "",
            })
        elif isinstance(step, str):
            steps.append({"action": step, "tool": ""})
    return steps


def tool_set(proposal: dict) -> set[str]:
    tools = {t.strip().lower() for t in proposal.get("required_tools") or [] if isinstance(t, str) and t.strip()}
    tools |= {s["tool"].strip().lower() for s in step_list(proposal) if s["tool"].strip()}
    return tools


def describe_tools(tools) -> str:
    """Only short, plain tool names are echoed back; anything else is counted."""
    names = sorted(t for t in tools if SAFE_TOOL_NAME.match(t))
    hidden = len(tools) - len(names)
    if hidden:
        names.append(f"{hidden} other")
    return ", ".join(names)


def contract_problems(proposal, title: str = "", proposal_id: str | None = None) -> list[str]:
    """Reasons the proposal is NOT an explicit GitHub PR merged -> Slack
    automation. An empty list means it fits the template."""
    if not isinstance(proposal, dict):
        return ["candidate has no stored proposal"]

    problems = []
    trigger = normalize_trigger(proposal.get("trigger"))
    if not trigger:
        problems.append("candidate has no trigger")
    elif trigger != SUPPORTED_TRIGGER:
        problems.append("only GITHUB_PULL_REQUEST_MERGED triggers are supported")

    steps = step_list(proposal)
    tools = tool_set(proposal)
    texts = [s["action"] for s in steps]
    texts += [t for t in proposal.get("expected_side_effects") or [] if isinstance(t, str)]
    title = title if isinstance(title, str) else ""

    if not steps:
        problems.append("candidate has no proposed steps")
    elif "slack" not in tools and not any(SLACK_WORD.search(t) for t in texts):
        problems.append(NOT_SLACK)
    if tools - ALLOWED_TOOLS:
        problems.append(f"{OTHER_TOOLS} (found: {describe_tools(tools - ALLOWED_TOOLS)})")
    if any(OTHER_SERVICES.search(t) for t in texts + [title]):
        problems.append(OTHER_SERVICE)

    if "resolution" in proposal:
        problems += resolution_problems(proposal, proposal_id)
    return problems


def is_supported(proposal, title: str = "") -> bool:
    return not contract_problems(proposal, title)


def valid_destination(destination) -> bool:
    return (
        isinstance(destination, dict)
        and destination.get("source") == DESTINATION_SOURCE
        and isinstance(destination.get("allowed_repository"), str)
        and bool(REPOSITORY_PATTERN.fullmatch(destination["allowed_repository"]))
        and isinstance(destination.get("slack_channel_id"), str)
        and bool(CHANNEL_ID_PATTERN.fullmatch(destination["slack_channel_id"]))
    )


def resolution_problems(proposal: dict, proposal_id: str | None = None) -> list[str]:
    """A resolved proposal must carry exactly the typed contract below."""
    resolution = proposal.get("resolution")
    if not isinstance(resolution, dict):
        return ["resolution record is malformed"]
    problems = []
    expected = {
        "kind": RESOLUTION_KIND,
        "version": RESOLUTION_VERSION,
        "template_type": TEMPLATE_TYPE,
        "trigger": SUPPORTED_TRIGGER,
        "action": SLACK_ACTION,
        "tools": sorted(ALLOWED_TOOLS),
    }
    for key, value in expected.items():
        if resolution.get(key) != value:
            problems.append(f"resolution {key} does not match the supported template")
    resolved_from = resolution.get("resolved_from")
    own_id = proposal_id or proposal.get("proposal_id")
    if not isinstance(resolved_from, str) or not PROPOSAL_ID_PATTERN.match(resolved_from):
        problems.append("resolution does not name the original candidate")
    elif resolved_from == own_id:
        problems.append("a resolved proposal must have a new proposal_id")
    if not valid_destination(resolution.get("destination")):
        problems.append("resolution destination is missing or invalid")
    if sorted(t.strip().lower() for t in proposal.get("required_tools") or [] if isinstance(t, str)) != sorted(ALLOWED_TOOLS):
        problems.append("resolved proposal must require exactly github and slack")
    if proposal.get("auto_execute_allowed") is not False or proposal.get("human_approval_required") is not True:
        problems.append("resolved proposal must require human approval and forbid auto-execution")
    return problems


def approved_destination(proposal) -> dict | None:
    """The destination a resolved proposal was approved with, or None for a
    proposal that is not a resolution."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("resolution"), dict):
        return None
    destination = proposal["resolution"].get("destination")
    if not valid_destination(destination):
        return None
    return {
        "allowed_repository": destination["allowed_repository"],
        "slack_channel_id": destination["slack_channel_id"],
    }


def destination_mismatch(proposal, config: dict | None) -> bool:
    """True when a resolved proposal's setup differs from what was approved."""
    destination = approved_destination(proposal)
    if destination is None or config is None:
        return False
    return any(config.get(key) != value for key, value in destination.items())


def resolution_blockers(row: dict) -> list[str]:
    """Why an (original) candidate cannot be resolved into a Slack proposal."""
    proposal = row.get("proposal")
    if not isinstance(proposal, dict):
        return ["candidate has no stored proposal"]
    blockers = []
    if row.get("review_status") != "approved":
        blockers.append("only approved candidates can be resolved")
    if (row.get("validation_result") or proposal.get("validation_result")) == "blocked":
        blockers.append("candidate is blocked by SOP validation")
    if "resolution" in proposal:
        blockers.append("candidate is already a resolved proposal")
    if normalize_trigger(proposal.get("trigger")) != SUPPORTED_TRIGGER:
        blockers.append("only GITHUB_PULL_REQUEST_MERGED triggers are supported")
    texts = [s["action"] for s in step_list(proposal)]
    texts += [t for t in proposal.get("expected_side_effects") or [] if isinstance(t, str)]
    texts += [t for t in tool_set(proposal)]
    if any(OTHER_SERVICES.search(t) for t in texts + [str(row.get("title") or "")]):
        blockers.append(OTHER_SERVICE)
    evidence = row.get("evidence_event_ids") or proposal.get("evidence_event_ids")
    if not isinstance(evidence, list) or not evidence:
        blockers.append("candidate has no evidence event IDs")
    return blockers


def resolved_proposal_id(original_id: str, repository: str, channel_id: str) -> str:
    """Deterministic: resolving the same candidate and destination twice
    yields the same id, so it can only be created once."""
    digest = hashlib.sha256(f"{original_id}|{repository}|{channel_id}".encode()).hexdigest()[:8]
    return f"{original_id[:100]}-slack-{digest}"


PRESERVED_FIELDS = (
    "department", "sop_constraints", "risk_level", "reversibility", "confidence",
    "expected_minutes_saved_per_run", "rollback_plan", "detection_settings",
)
ORIGINAL_SNAPSHOT_FIELDS = ("title", "steps", "required_tools", "expected_side_effects", "preconditions")


def build_resolved_proposal(row: dict, config: dict, resolved_at: str) -> dict:
    """New proposal for the supported template. The destination comes only
    from the reviewer's saved setup (config); everything about evidence, SOP
    and unresolved risk is carried over unchanged."""
    original = row["proposal"]
    original_id = row["proposal_id"]
    repository = config["allowed_repository"]
    channel_id = config["slack_channel_id"]
    validation = row.get("validation_result") or original.get("validation_result")
    sop_status = row.get("sop_status") or original.get("sop_status")
    evidence = list(row.get("evidence_event_ids") or original.get("evidence_event_ids") or [])
    blocked_reasons = [r for r in original.get("blocked_reasons") or [] if isinstance(r, str)]

    proposal = {key: original[key] for key in PRESERVED_FIELDS if key in original}
    proposal.update({
        "proposal_id": resolved_proposal_id(original_id, repository, channel_id),
        "title": "Slack message when a GitHub pull request is merged (resolved)",
        "problem_summary": (
            f"Resolved from candidate {original_id}. Approved destination: "
            f"repository {repository}, Slack channel {channel_id}. "
            "Approving this proposal approves this action and destination."
        ),
        "evidence_event_ids": evidence,
        "trigger": SUPPORTED_TRIGGER,
        "preconditions": [
            "The connected Slack account can post to the approved channel (checked at activation)",
            "Pull request events come from the simulated activity source",
        ],
        "steps": [
            {"step_number": 1, "tool": "github",
             "action": "Receive the GITHUB_PULL_REQUEST_MERGED event for the approved repository only"},
            {"step_number": 2, "tool": "github",
             "action": "Read the pull request title, author, repository and merge time from the event"},
            {"step_number": 3, "tool": "slack",
             "action": f"Send one Slack message ({SLACK_ACTION}) to the approved Slack channel"},
        ],
        "required_tools": sorted(ALLOWED_TOOLS),
        "expected_side_effects": [
            "One Slack message is posted to the approved Slack channel for each merged pull request",
        ],
        "sop_status": sop_status,
        "validation_result": validation,
        "human_approval_required": True,
        "auto_execute_allowed": False,
        "blocked_reasons": blocked_reasons + [
            f"Resolved from generic candidate {original_id}: it did not name the destination. "
            "Slack and the channel come from the reviewer's saved setup and need this fresh approval.",
            "Only the Slack message is in scope. Any other action the original proposal allowed "
            "(for example updating a record or task) is not included and needs its own candidate.",
            "Slack account and channel access are checked at activation, not by this approval.",
        ],
        "approval_question": (
            f"Approve sending one Slack message to channel {channel_id} for each pull request "
            f"merged in {repository}? SOP status: {sop_status}; SOP validation: {validation}."
        ),
        "resolution": {
            "kind": RESOLUTION_KIND,
            "version": RESOLUTION_VERSION,
            "resolved_from": original_id,
            "original_review_status": row.get("review_status"),
            "original": {key: original.get(key) for key in ORIGINAL_SNAPSHOT_FIELDS},
            "template_type": TEMPLATE_TYPE,
            "trigger": SUPPORTED_TRIGGER,
            "action": SLACK_ACTION,
            "tools": sorted(ALLOWED_TOOLS),
            "destination": {
                "source": DESTINATION_SOURCE,
                "allowed_repository": repository,
                "slack_channel_id": channel_id,
            },
            "resolved_at": resolved_at,
        },
    })
    return proposal
