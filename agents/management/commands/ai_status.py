"""`python manage.py ai_status` -- one command that answers "is the AI
actually working right now, and if not, why?".

Worth having because the failure mode is silent by design: when Gemini is
rate-limited the agents keep working from the offline router, so nothing
crashes and there's no traceback to read. This prints what the runtime
actually sees -- key present or not, which models are in the chain, which
ones are on cooldown or retired -- and optionally sends one real request.
"""
import os

from django.core.management.base import BaseCommand

import llm_client


class Command(BaseCommand):
    help = "Show the AI connection status (key, model chain, cooldowns) and optionally test it live."

    def add_arguments(self, parser):
        parser.add_argument(
            "--test", action="store_true",
            help="Send one real request to check the key and model actually work.",
        )

    def handle(self, *args, **options):
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.stdout.write(self.style.MIGRATE_HEADING("AI connection"))
        if llm_client.offline_mode():
            self.stdout.write("  Mode:        OFFLINE (AI_OFFLINE_MODE is set) -- rule-based engine only")
        elif not key:
            self.stdout.write("  Key:         missing (set GEMINI_API_KEY in .env)")
        else:
            self.stdout.write(f"  Key:         present ({key[:6]}…{key[-4:]}, {len(key)} chars)")

        self.stdout.write(f"  Model chain: {', '.join(llm_client.model_chain())}")

        state = llm_client.cooldown_status()
        if state["retired"]:
            self.stdout.write(self.style.WARNING(f"  Retired:     {', '.join(state['retired'])} (returned 404)"))
        if state["cooling"]:
            cooling = ", ".join(f"{m} ({s:.0f}s left)" for m, s in state["cooling"].items())
            self.stdout.write(self.style.WARNING(f"  Rate-limited: {cooling}"))
        if not state["retired"] and not state["cooling"]:
            self.stdout.write("  Cooldowns:   none")

        self.stdout.write(
            "\n  Note: when no model can answer, both agents keep working through the\n"
            "  offline rule-based router (agents/nlu.py) -- every tool stays reachable."
        )

        if not options["test"]:
            self.stdout.write("\n  Run with --test to send one real request.")
            return

        self.stdout.write(self.style.MIGRATE_HEADING("\nLive test"))
        if not llm_client.llm_available():
            self.stdout.write(self.style.ERROR("  Skipped: no usable key / offline mode."))
            return
        try:
            reply = llm_client.generate_text(
                "Reply with exactly: OK", "Say OK.", max_tokens=20,
            )
            self.stdout.write(self.style.SUCCESS(f"  Worked. Model replied: {reply.strip()[:60]}"))
            self.stdout.write(f"  Model used:  {llm_client.cooldown_status()['last_good_model']}")
        except llm_client.LLMUnavailableError as e:
            self.stdout.write(self.style.ERROR(f"  Failed: {e}"))
            self.stdout.write("  The app still runs -- the agents fall back to the offline engine.")
