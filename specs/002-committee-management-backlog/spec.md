# Feature Specification: Committee Management Backlog

**Feature Branch**: `002-committee-management-backlog`  
**Created**: 2026-06-28  
**Status**: Backlog exploration  
**Input**: User idea to expand meeting agenda handling into committee management while keeping the current bot MVP lean.

## Summary

Committee management is a possible future direction for SCLK Notifier. The idea is to help the Alumni Board turn meeting agenda topics into follow-up work that can be carried over, delegated, resolved, or revisited later.

This is not part of the current reminder and agenda MVP. The current priority remains a small working Discord bot for scheduled-event reminders, agenda collection, and basic board administration.

## Likely User Groups

- **Alumni members** can propose agenda topics through the existing agenda workflow.
- **Alumni Board members** can manage topics, decide what needs follow-up, and keep board meeting time focused.
- **Committee leads** may eventually own delegated topics or report back on open follow-ups.

## Candidate Workflows

- Convert an agenda item into a committee follow-up.
- Assign a follow-up to an owner, committee, or board member.
- Carry an unresolved topic to a future meeting.
- Mark a topic closed, resolved, or no longer needed.
- View open committee follow-ups before or during a board meeting.
- Triage agenda topics so recurring or deep topics do not consume every meeting.

## Architecture Posture

If this idea moves forward, prefer a monolith-first approach:

- Extend the existing Discord slash-command bot before adding a web app, dashboard, service split, or separate worker.
- Keep roles simple at first: Alumni members, Alumni Board members, and possibly committee leads.
- Avoid building a highly configurable role and permission platform until at least two real committee workflows require it.
- Treat configurability as a later pressure valve, not the starting point.

This keeps the project from becoming a broad board-operations platform before the core reminder and agenda MVP is stable.

## Decision Gates

Before implementation, answer these questions:

1. Does committee management belong in this bot, or should it be a separate tool?
2. Are committees formal Discord roles, simple labels, or a mix of both?
3. Should delegated topics be visible to all alumni, only Alumni Board members, or only assigned committees?
4. Should this stay slash-command only, or would committee management eventually need a dashboard?
5. What is the smallest workflow that would make board meetings noticeably easier?
6. Does the extra workflow make the bot too broad for volunteers to maintain?

## Not In Scope For The Current MVP

- New slash commands.
- New database tables or lifecycle/status fields.
- New Discord channels or channel permissions.
- New role IDs or configurable role hierarchy.
- Web dashboards or external integrations.
- Automated committee assignment or recurring workflow automation.

## Validation For This Planning Item

- The idea is visible in the backlog without changing bot behavior.
- MVP agenda and reminder work remains clearly separate from speculative committee management.
- Future implementers can see the preferred starting posture: simple Discord slash commands in the existing bot, not a new platform.
