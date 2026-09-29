"""Tests for the two things that decide whether this project survives a
rate-limited API key: the offline intent router, and the transport's
failure handling.

Run with:  python manage.py test agents
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

import llm_client
from agents import student_agent, instructor_agent
from courses.models import Course, Topic

User = get_user_model()


class _FakeAPIError(Exception):
    def __init__(self, code, message="error"):
        super().__init__(message)
        self.code = code


def _fake_errors_module():
    return type("errors", (), {"APIError": _FakeAPIError})


class OfflineRouterTests(TestCase):
    """With no API key at all, every agent capability must still be reachable
    from a plain typed sentence -- that is the whole point of the fallback."""

    def setUp(self):
        self.instructor = User.objects.create_user(
            username="prof", password="x", role=User.Role.INSTRUCTOR)
        self.student = User.objects.create_user(
            username="stud", password="x", role=User.Role.STUDENT)
        self.course = Course.objects.create(title="Full-Stack Python", instructor=self.instructor)
        self.course.students.add(self.student)
        self.topic = Topic.objects.create(course=self.course, name="Django ORM")
        self.env = mock.patch.dict("os.environ", {"GEMINI_API_KEY": "", "AI_OFFLINE_MODE": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def _student_tools(self, message):
        return [c["name"] for c in student_agent.handle_message(self.student, message)["tool_calls"]]

    def _instructor_tools(self, message):
        return [c["name"] for c in instructor_agent.handle_message(self.instructor, message)["tool_calls"]]

    def test_student_intents_reach_the_right_tool(self):
        cases = {
            "what's my performance?": "get_performance",
            "create a study plan for me": "create_study_plan",
            "show me my dashboard": "get_learning_dashboard",
            "what should I study next?": "get_recommendations",
            "explain Django ORM": "explain_topic",
            # Arabic phrasings go through the same router.
            "مستواي ايه؟": "get_performance",
            "اعملي خطة مذاكرة": "create_study_plan",
            "اشرح Django ORM": "explain_topic",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertIn(expected, self._student_tools(message))

    def test_instructor_intents_reach_the_right_tool(self):
        cases = {
            "show me my course analytics": "get_course_analytics",
            "who is at risk?": "get_at_risk_students",
            "how is the class doing?": "analyze_class_performance",
            "summarize the course": "generate_course_summary",
            'announce that "the exam moved to Thursday"': "send_announcement",
            "احصائيات الكورس": "get_course_analytics",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertIn(expected, self._instructor_tools(message))

    def test_unrecognised_message_explains_itself_instead_of_erroring(self):
        reply = student_agent.handle_message(self.student, "asdfgh")["reply"]
        self.assertIn("study plan", reply.lower())

    def test_router_never_raises_for_arbitrary_input(self):
        for junk in ["", "?????", "12345", "delete", "quiz", "🙂"]:
            with self.subTest(junk=junk):
                self.assertIn("reply", student_agent.handle_message(self.student, junk))
                self.assertIn("reply", instructor_agent.handle_message(self.instructor, junk))


class TransportResilienceTests(TestCase):
    """The client must classify API errors correctly and never stall the
    request once it knows the answer will be 429."""

    def setUp(self):
        llm_client._cooldown_until.clear()
        llm_client._retired_models.clear()
        llm_client._last_good_model = None
        self.env = mock.patch.dict(
            "os.environ", {"GEMINI_API_KEY": "test-key", "AI_OFFLINE_MODE": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.errors_patch = mock.patch.object(llm_client, "genai_errors", _fake_errors_module())
        self.errors_patch.start()
        self.addCleanup(self.errors_patch.stop)

    def _install(self, generate_content):
        client = mock.Mock()
        client.models.generate_content.side_effect = generate_content
        return mock.patch.object(llm_client, "_client", lambda: client)

    def test_rate_limited_model_is_skipped_and_the_next_one_answers(self):
        chain = llm_client.model_chain()

        def gen(model, contents, config):
            if model == chain[0]:
                raise _FakeAPIError(429, "RESOURCE_EXHAUSTED: quota exceeded per day")
            return mock.Mock(candidates=[mock.Mock(
                content=mock.Mock(parts=[mock.Mock(text="ok", function_call=None)]))])

        with self._install(gen):
            self.assertEqual(llm_client.generate_text("s", "u"), "ok")
        self.assertIn(chain[0], llm_client.cooldown_status()["cooling"])

    def test_every_model_down_fails_fast_without_sleeping(self):
        def gen(model, contents, config):
            raise _FakeAPIError(429, "quota exceeded per day")

        with self._install(gen):
            with self.assertRaises(llm_client.LLMUnavailableError):
                llm_client.generate_text("s", "u")

            # Second call must not touch the network at all: every model is
            # on cooldown, so we go straight to the offline engine.
            with mock.patch.object(llm_client, "_client") as spy:
                with self.assertRaises(llm_client.LLMUnavailableError):
                    llm_client.generate_text("s", "u")
                spy.assert_not_called()

    def test_a_retired_model_name_is_dropped_permanently(self):
        chain = llm_client.model_chain()

        def gen(model, contents, config):
            if model == chain[0]:
                raise _FakeAPIError(404, "model not found")
            return mock.Mock(candidates=[mock.Mock(
                content=mock.Mock(parts=[mock.Mock(text="ok", function_call=None)]))])

        with self._install(gen):
            llm_client.generate_text("s", "u")
        self.assertIn(chain[0], llm_client.cooldown_status()["retired"])

    def test_a_rejected_key_stops_the_walk_immediately(self):
        calls = []

        def gen(model, contents, config):
            calls.append(model)
            raise _FakeAPIError(403, "API key not valid")

        with self._install(gen):
            with self.assertRaises(llm_client.LLMUnavailableError):
                llm_client.generate_text("s", "u")
        self.assertEqual(len(calls), 1, "a bad key is bad for every model -- don't try them all")


class AgentDegradationTests(TestCase):
    """When the model is unreachable mid-conversation, the agent still
    performs the action instead of returning an apology."""

    def setUp(self):
        self.student = User.objects.create_user(
            username="stud2", password="x", role=User.Role.STUDENT)
        self.course = Course.objects.create(
            title="Full-Stack Python",
            instructor=User.objects.create_user(username="prof2", password="x", role=User.Role.INSTRUCTOR),
        )
        self.course.students.add(self.student)

    def test_quota_error_still_runs_the_requested_action(self):
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "k", "AI_OFFLINE_MODE": "0"}), \
             mock.patch.object(student_agent, "run_tool_loop",
                               side_effect=llm_client.LLMUnavailableError("over quota")):
            result = student_agent.handle_message(self.student, "what's my performance?")

        self.assertEqual([c["name"] for c in result["tool_calls"]], ["get_performance"])
        # The action succeeded, so the user isn't shown a degradation warning.
        self.assertNotIn("⚠", result["reply"])
