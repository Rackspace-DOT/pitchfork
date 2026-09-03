
import os
import subprocess
import unittest


JS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), 'js')
)
JS_TEST = os.path.join(JS_DIR, 'duplicate_rows.test.js')
INSTALL_HINT = 'cd %s && npm install' % os.path.join('tests', 'js')


def run_node(args):
    """Run node, returning (returncode, combined output).

    Returns (None, reason) when node itself is unavailable.
    """
    try:
        process = subprocess.Popen(
            ['node'] + args,
            cwd=JS_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
    except OSError as error:
        return None, str(error)

    output = process.communicate()[0]
    if not isinstance(output, str):
        output = output.decode('utf-8', 'replace')

    return process.returncode, output


def missing_requirement():
    """Return a skip reason, or None when the JS tests can run."""
    code, output = run_node(['--version'])
    if code is None:
        return 'node is not installed (%s)' % output
    if code != 0:
        return 'node --version failed: %s' % output.strip()

    code, output = run_node([
        '-e', "require.resolve('jsdom'); require.resolve('jquery')"
    ])
    if code != 0:
        return 'jsdom and jquery are not installed -- run: %s' % INSTALL_HINT

    return None


class DuplicateRowsJsTests(unittest.TestCase):
    """Drives tests/js/duplicate_rows.test.js.

    The duplicate-row cloning in ui.js is not reachable from the
    Python suite, and PR #68 shipped a break in it that every Python test
    passed through. This wrapper keeps that JS covered by the normal test
    command, and skips with instructions when node or its packages are absent
    so it never blocks a Python-only checkout.
    """

    def test_duplicate_row_cloning(self):
        reason = missing_requirement()
        if reason is not None:
            raise unittest.SkipTest(reason)

        self.assertTrue(
            os.path.exists(JS_TEST),
            '%s is missing' % JS_TEST
        )

        code, output = run_node([JS_TEST])
        self.assertEqual(
            code,
            0,
            'duplicate-row JS tests failed:\n%s' % output
        )


if __name__ == '__main__':
    unittest.main()
