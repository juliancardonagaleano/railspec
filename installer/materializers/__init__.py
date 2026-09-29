"""Materializers for the ``.claude/`` mirror (unit 0006).

Modules:
    - ``agents`` — fuses ``.agents/agents/<rol>.md`` + ``.spec/perfiles.yaml``
      into ``.claude/agents/<rol>.md`` and its ``<rol>-<effort>`` variants.
    - ``commands`` — byte-for-byte copies ``.agents/commands/*.md`` to
      ``.claude/commands/<name>.md`` with a generated-header comment.
    - ``skills`` — byte-for-byte copies every portable skill under
      ``.agents/skills/<prefix>*/`` (with SKILL.md) into
      ``.claude/skills/<prefix>*/``.

These three modules are the canonical homes of the logic that used to live
in ``scripts/materialize_claude_{agents,commands,skills}.py``. The shims
under ``scripts/`` (≤60 lines each) delegate here so the legacy entry
points keep working without re-importing argparse or duplicating the
contract — see CA-01, CA-10, CA-11.
"""