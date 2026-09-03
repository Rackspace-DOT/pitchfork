
import os
import sys
import types
import unittest

try:
    import importlib.util
except Exception:
    importlib = None
    import imp


admin_defaults = types.ModuleType('flask_cloudadmin.defaults')
admin_defaults.check_and_initialize = lambda app, database: {
    'application_set': True
}
sys.modules['flask_cloudadmin'] = types.ModuleType('flask_cloudadmin')
sys.modules['flask_cloudadmin.defaults'] = admin_defaults

config_module = types.ModuleType('config')


class TestConfig(object):
    ADMIN_USERNAME = 'admin'
    ADMIN_NAME = 'Admin'
    ADMIN_EMAIL = 'admin@example.com'


config_module.config = TestConfig()
sys.modules['config'] = config_module

ROOT = os.path.join(os.path.dirname(__file__), '..')
DEFAULTS_PATH = os.path.join(ROOT, 'pitchfork', 'defaults.py')
FUNCTIONS_PATH = os.path.join(ROOT, 'pitchfork', 'template_functions.py')


def load_module(name, path):
    if importlib is None:
        return imp.load_source(name, path)

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


defaults = load_module('pitchfork_defaults_for_limit_tests', DEFAULTS_PATH)


def load_duplicate_helpers():
    """Pull duplicate helpers out of the template context processor.

    template_functions imports pitchfork.models, which is not reachable
    without the internal admin package, so stub the one name it needs.
    utility_processor() only builds a dict of closures, so no application
    context is required to call it.
    """
    stubbed = ['pitchfork', 'pitchfork.models', 'models']
    saved = {
        name: sys.modules[name] for name in stubbed if name in sys.modules
    }

    try:
        package = types.ModuleType('pitchfork')
        package.__path__ = []
        sys.modules['pitchfork'] = package

        models = types.ModuleType('pitchfork.models')
        models.Product = object
        sys.modules['pitchfork.models'] = models
        sys.modules['models'] = models

        functions = load_module(
            'pitchfork_template_functions_for_tests', FUNCTIONS_PATH
        )
        helpers = functions.utility_processor()
        return (
            helpers['duplicate_row_limit'],
            helpers['duplicate_group_row_limit']
        )
    finally:
        for name in stubbed:
            if name in saved:
                sys.modules[name] = saved[name]
            else:
                sys.modules.pop(name, None)


def load_duplicate_row_limit():
    return load_duplicate_helpers()[0]


class DuplicateRowLimitTests(unittest.TestCase):
    """The row cap the browser is handed.

    process_api_data_request only fills placeholders that already exist in
    data_object, so a row the body has no slot for is dropped from the request
    without an error. Deriving the cap from the body is what keeps the UI from
    offering rows that would silently go nowhere.
    """

    def setUp(self):
        (
            self.duplicate_row_limit,
            self.duplicate_group_row_limit
        ) = load_duplicate_helpers()

    def test_shipped_bulk_calls_advertise_every_row_the_body_holds(self):
        bulk_calls = [
            call for call in defaults.RACKCONNECT_EXTRA_API_CALLS
            if (call.get('verb'), call.get('api_uri'))
            in defaults.RACKCONNECT_BULK_API_CALL_KEYS
        ]

        self.assertEqual(len(bulk_calls), 4)
        for call in bulk_calls:
            first_variable = call.get('variables')[0].get('variable_name')
            self.assertEqual(
                self.duplicate_row_limit(call, first_variable),
                defaults.BULK_ROW_COUNT - 1,
                '%s should allow %d rows' % (
                    call.get('title'), defaults.BULK_ROW_COUNT
                )
            )

    def test_three_slot_body_allows_three_rows(self):
        """The shape of the autoscale and servers network fields."""
        call = {
            'data_object': (
                '{\r\n'
                '    "networks": [\r\n'
                '        {"uuid": "{network_uuid}"},\r\n'
                '        {"uuid": "{network_uuid_1}"},\r\n'
                '        {"uuid": "{network_uuid_2}"}\r\n'
                '    ]\r\n'
                '}'
            )
        }

        self.assertEqual(
            self.duplicate_row_limit(call, 'network_uuid'), 2
        )

    def test_body_without_suffixed_slots_allows_no_rows(self):
        call = {'data_object': '{"uuid": "{network_uuid}"}'}

        self.assertEqual(
            self.duplicate_row_limit(call, 'network_uuid'), 0
        )

    def test_missing_or_empty_body_allows_no_rows(self):
        self.assertEqual(self.duplicate_row_limit({}, 'network_uuid'), 0)
        self.assertEqual(
            self.duplicate_row_limit({'data_object': ''}, 'network_uuid'), 0
        )
        self.assertEqual(
            self.duplicate_row_limit({'data_object': None}, 'network_uuid'), 0
        )

    def test_missing_variable_name_allows_no_rows(self):
        call = {'data_object': '{"uuid": "{network_uuid_1}"}'}

        self.assertEqual(self.duplicate_row_limit(call, ''), 0)
        self.assertEqual(self.duplicate_row_limit(call, None), 0)

    def test_uri_placeholders_count_for_bodyless_calls(self):
        call = {
            'api_uri': '/servers/{server_id}/networks/{network_uuid}'
                       '/{network_uuid_1}/{network_uuid_2}',
            'data_object': ''
        }

        self.assertEqual(
            self.duplicate_row_limit(call, 'network_uuid'), 2
        )

    def test_custom_header_placeholders_count_for_header_calls(self):
        call = {
            'custom_header_value': (
                'node={network_uuid}; node={network_uuid_1}'
            )
        }

        self.assertEqual(
            self.duplicate_row_limit(call, 'network_uuid'), 1
        )

    def test_limit_is_per_variable(self):
        """A variable with no slots of its own must not borrow another's.

        Every variable in a group is cloned together, so a body that suffixes
        one but not another would drop half of each new row.
        """
        call = {
            'data_object': (
                '[{"a": "{cloud_server_id}", "b": "{port}"},'
                ' {"a": "{cloud_server_id_1}"}]'
            )
        }

        self.assertEqual(
            self.duplicate_row_limit(call, 'cloud_server_id'), 1
        )
        self.assertEqual(self.duplicate_row_limit(call, 'port'), 0)

    def test_group_limit_uses_the_shortest_member(self):
        call = {
            'variables': [
                {
                    'variable_name': 'cloud_server_id',
                    'duplicate_group': 'nodes'
                }, {
                    'variable_name': 'port',
                    'duplicate_group': 'nodes'
                }
            ],
            'data_object': (
                '[{"a": "{cloud_server_id}", "b": "{port}"},'
                ' {"a": "{cloud_server_id_1}", "b": "{port_1}"},'
                ' {"a": "{cloud_server_id_2}"}]'
            )
        }

        self.assertEqual(
            self.duplicate_group_row_limit(
                call,
                call.get('variables')[0]
            ),
            1
        )

    def test_prefix_matches_do_not_count(self):
        call = {'data_object': '{"a": "{cloud_server_id_extra_1}"}'}

        self.assertEqual(
            self.duplicate_row_limit(call, 'cloud_server_id'), 0
        )

    def test_multi_digit_suffixes_are_read_whole(self):
        """A wider body must not be read as a single digit."""
        slots = ['"{node}"']
        slots.extend(
            '"{node_%d}"' % index for index in range(1, 13)
        )
        call = {
            'data_object': '{"a": [%s]}' % ','.join(slots)
        }

        self.assertEqual(self.duplicate_row_limit(call, 'node'), 12)

    def test_gaps_stop_at_the_last_contiguous_suffix(self):
        call = {
            'data_object': (
                '{"a": "{node}", "b": "{node_1}", "c": "{node_3}"}'
            )
        }

        self.assertEqual(self.duplicate_row_limit(call, 'node'), 1)

    def test_regex_characters_in_a_variable_name_are_literal(self):
        call = {
            'data_object': (
                '{"a": "{od.d+}", "b": "{od.d+_1}", '
                '"c": "{od.d+_2}", "d": "{od.d+_3}"}'
            )
        }

        self.assertEqual(self.duplicate_row_limit(call, 'od.d+'), 3)
        self.assertEqual(self.duplicate_row_limit(call, 'odxdx'), 0)


if __name__ == '__main__':
    unittest.main()
