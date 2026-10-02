import unittest

import zcode_cli_launcher as launcher


REQUIRED_HOSTED_ONLY = {
    "/fast", "/deep-research", "/usage-credits", "/extra-usage",
    "/upgrade", "/rate-limit-options", "/limit-reset", "/passes",
    "/powerup", "/pro-trial-expired", "/privacy-settings",
    "/schedule", "/autofix-pr", "/remote-env", "/remote-control",
    "/__remote-workflow", "/workflow-launch-exec", "/team-onboarding",
    "/design", "/design-sync", "/design-consent", "/design-revoke",
    "/design-login", "/cloud-plugins", "/install-github-app",
    "/install-slack-app", "/setup-bedrock", "/setup-vertex",
    "/web-setup", "/feedback", "/bug", "/login", "/logout",
}


class ClaudeSlashCompatibilityTests(unittest.TestCase):
    def test_known_hosted_only_catalog_is_covered(self):
        self.assertTrue(REQUIRED_HOSTED_ONLY <= set(launcher.CLAUDE_UNSUPPORTED_SLASH_COMMANDS))

    def test_usage_is_launcher_owned(self):
        self.assertEqual(
            launcher.claude_launcher_intercept("/usage"),
            ("usage", "/usage", "", ()),
        )

    def test_every_hosted_only_command_is_intercepted(self):
        for command, (title, detail) in launcher.CLAUDE_UNSUPPORTED_SLASH_COMMANDS.items():
            with self.subTest(command=command):
                intercept = launcher.claude_launcher_intercept(command)
                self.assertIsNotNone(intercept)
                self.assertEqual(intercept[0], "unsupported")
                self.assertEqual(intercept[1], command)
                self.assertEqual(intercept[2], title)
                self.assertEqual(intercept[3], detail)

    def test_command_arguments_do_not_bypass_interception(self):
        intercept = launcher.claude_launcher_intercept("/fast unexpected-arg")
        self.assertIsNotNone(intercept)
        self.assertEqual(intercept[:2], ("unsupported", "/fast"))

    def test_command_matching_is_case_insensitive(self):
        intercept = launcher.claude_launcher_intercept("/LOGIN")
        self.assertIsNotNone(intercept)
        self.assertEqual(intercept[:2], ("unsupported", "/login"))

    def test_normal_local_commands_pass_through(self):
        local_commands = (
            "/config",
            "/context",
            "/mcp",
            "/plugin",
            "/model glm-5.3",
            "/effort",
            "/compact",
            "/clear",
            "/rename",
            "/reload-plugins",
            "/reload-skills",
            "/code-review",
            "/security-review",
            "/agents",
            "/list-agents",
            "/loop",
            "/init",
        )
        for command in local_commands:
            with self.subTest(command=command):
                self.assertIsNone(launcher.claude_launcher_intercept(command))

    def test_plain_prompts_pass_through(self):
        self.assertIsNone(launcher.claude_launcher_intercept("fix the tests"))
        self.assertIsNone(launcher.claude_launcher_intercept(""))


if __name__ == "__main__":
    unittest.main()
