from __future__ import annotations

from research_system.cli.chat_updates import dispatch_transcript_update, meta_update

CHAT_COMMAND_SUMMARY_LINES = (
    "/commands                 open or close this popup",
    "/help                     print command help into the transcript",
    "/new                      start a new persistent chat thread",
    "/fork                     fork the active chat thread",
    "/thread-archive [id]      archive current or target chat thread",
    "/thread-unarchive <id>    unarchive a chat thread",
    "/thread [thread_id]       show or switch active chat thread",
    "/threads [limit] [scope]  list active/all/archived threads",
    "/compact                  summarize active thread into compact context",
    "/stop                     stop the current run",
    "/intent <intent|auto>     set default intent",
    "/run-id <run_id|clear>    set review target run",
    "/thinking <on|off>        toggle thinking stream",
    "/events <off|compact|full> set event visibility",
    "/run <query>              run a query explicitly",
    "/review <run_id> <query>  run review_thesis",
    "/history [limit] [status] show run history",
    "/proposals [limit] [status] list methodology proposals",
    "/proposal <proposal_id>   show proposal detail",
    "/review-proposal <id> <approve|reject> [reason]",
    "/replay <run_id|last>     replay a run",
    "/trace <run_id|last>      inspect run subagent/dynamic trace",
    "/resume <run_id|last>     show checkpoint resumability",
    "/cancel <run_id|last>     cancel a run",
    "/status | /doctor | /check",
    "/cleanup | /reindex | /last | /clear | /exit",
)


CHAT_HELP_LINES = [
    "Commands:",
    "  /help                         show this help",
    "  /exit                         exit interactive mode",
    "  /new                          start a new persistent chat thread",
    "  /fork                         fork the active chat thread",
    "  /thread-archive [thread_id]  archive the current or target chat thread",
    "  /thread-unarchive <thread_id> unarchive a chat thread",
    "  /thread [thread_id]          show or switch the active chat thread",
    "  /threads [limit] [scope]     list active, all, or archived chat threads",
    "  /compact                      summarize the active thread into reusable context",
    "  /stop                         stop the current run from chat",
    "  /intent <intent|auto>        set default intent for plain text input",
    "  /run-id <run_id|clear>       set review target run id for review_thesis",
    "  /thinking <on|off>           toggle visible thinking stream",
    "  /events <off|compact|full>   control event visibility",
    "  /run <query>                 run a query explicitly",
    "  /review <run_id> <query>     run review_thesis against a prior run",
    "  /history [limit] [status]    show recent runs",
    "  /proposals [limit] [status]  list methodology proposals",
    "  /proposal <proposal_id>      show a proposal detail",
    "  /review-proposal <id> <approve|reject> [reason]  review a proposal",
    "  /status                      show project status summary",
    "  /doctor                      show environment diagnostics",
    "  /check                       run local checks",
    "  /replay <run_id|last>        replay a run",
    "  /trace <run_id|last>         inspect run subagent/dynamic trace",
    "  /resume <run_id|last>        show checkpoint resumability",
    "  /cancel <run_id|last>        cancel a run",
    "  /cleanup                     run retention cleanup",
    "  /reindex                     rebuild local Qdrant index",
    "  /last                        show last run id and current session defaults",
    "  /clear                       clear current intent and review target run id",
]


def print_chat_help(*, output_writer=None) -> None:
    for line in CHAT_HELP_LINES:
        dispatch_transcript_update(output_writer, meta_update(line))
