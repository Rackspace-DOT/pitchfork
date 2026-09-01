
import json
import os
import sys
import types
import unittest
from copy import deepcopy

try:
    import importlib.util
except Exception:
    importlib = None
    import imp


admin_defaults = types.ModuleType('flask_raxinternaladmin.defaults')
admin_defaults.check_and_initialize = lambda app, database: {
    'application_set': True
}
sys.modules['flask_raxinternaladmin'] = types.ModuleType(
    'flask_raxinternaladmin'
)
sys.modules['flask_raxinternaladmin.defaults'] = admin_defaults

DEFAULTS_PATH = os.path.join(
    os.path.dirname(__file__),
    '..',
    'pitchfork',
    'defaults.py'
)
HELPER_PATH = os.path.join(
    os.path.dirname(__file__),
    '..',
    'pitchfork',
    'helper.py'
)
MODELS_PATH = os.path.join(
    os.path.dirname(__file__),
    '..',
    'pitchfork',
    'models.py'
)

if importlib is not None:
    spec = importlib.util.spec_from_file_location(
        'pitchfork_defaults_for_tests',
        DEFAULTS_PATH
    )
    defaults = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(defaults)
else:
    defaults = imp.load_source('pitchfork_defaults_for_tests', DEFAULTS_PATH)


def load_helper_module():
    if importlib is None:
        return imp.load_source('pitchfork_helper_for_tests', HELPER_PATH)

    module_names = [
        'pitchfork',
        'pitchfork.models',
        'pitchfork.forms',
        'pitchfork.cloud_dns_export',
        'pitchfork.url_safety'
    ]
    old_modules = {
        name: sys.modules[name] for name in module_names
        if name in sys.modules
    }

    try:
        pitchfork = types.ModuleType('pitchfork')
        pitchfork.__path__ = []
        sys.modules['pitchfork'] = pitchfork

        models = types.ModuleType('pitchfork.models')
        models.Variable = object
        sys.modules['pitchfork.models'] = models
        sys.modules['pitchfork.forms'] = types.ModuleType('pitchfork.forms')
        sys.modules['pitchfork.cloud_dns_export'] = types.ModuleType(
            'pitchfork.cloud_dns_export'
        )

        url_safety = types.ModuleType('pitchfork.url_safety')

        class UnsafeOutboundRequest(Exception):
            pass

        def noop(*args, **kwargs):
            return None

        url_safety.UnsafeOutboundRequest = UnsafeOutboundRequest
        for name in [
            '_has_userinfo',
            '_is_private_ipv4',
            '_is_private_ipv6',
            '_is_private_host',
            '_validate_outbound_url',
            '_build_api_url',
            '_validate_endpoint_hostname',
            'sanitize_query_filter'
        ]:
            setattr(url_safety, name, noop)
        sys.modules['pitchfork.url_safety'] = url_safety

        spec = importlib.util.spec_from_file_location(
            'pitchfork_helper_for_tests',
            HELPER_PATH
        )
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        return helper
    finally:
        for name in module_names:
            if name in old_modules:
                sys.modules[name] = old_modules[name]
            else:
                sys.modules.pop(name, None)


def load_models_module():
    if importlib is None:
        return imp.load_source('pitchfork_models_for_tests', MODELS_PATH)

    spec = importlib.util.spec_from_file_location(
        'pitchfork_models_for_tests',
        MODELS_PATH
    )
    models = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(models)
    return models


class FakeCollection(object):
    def __init__(self, docs=None):
        self.docs = docs or []
        self.inserted = []
        self.updated = []

    def find_one(self, query=None):
        query = query or {}
        for doc in self.docs:
            matches = True
            for key, value in query.items():
                if doc.get(key) != value:
                    matches = False
                    break
            if matches:
                return doc
        return None

    def insert(self, doc):
        self.docs.append(doc)
        self.inserted.append(doc)
        return 'inserted-id'

    def update(self, query, update):
        self.updated.append((query, update))
        doc = self.find_one(query)
        if doc is None:
            return
        set_values = update.get('$set', {})
        for key, value in set_values.items():
            parts = key.split('.')
            target = doc
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value


class FakeDb(object):
    def __init__(
        self,
        monitoring_docs=None,
        rackconnect_docs=None,
        api_settings_docs=None
    ):
        self.monitoring = FakeCollection(monitoring_docs)
        self.rack_connect = FakeCollection(rackconnect_docs)
        self.api_settings = FakeCollection(
            api_settings_docs or [
                {
                    'rackconnect': {
                        'groups': [
                            {
                                'slug': 'public_ips',
                                'name': 'Public IPs',
                                'order': 1
                            }, {
                                'slug': 'load_balancer_pools',
                                'name': 'Load Balancer Pools',
                                'order': 2
                            }, {
                                'slug': 'networks',
                                'name': 'Networks',
                                'order': 3
                            }
                        ]
                    }
                }
            ]
        )


class DefaultsTests(unittest.TestCase):
    def rackconnect_docs_with_pr67_bulk_calls(self):
        docs = deepcopy(defaults.RACKCONNECT_EXTRA_API_CALLS)
        for doc in docs:
            key = (doc.get('verb'), doc.get('api_uri'))
            if key not in defaults.RACKCONNECT_BULK_API_CALL_KEYS:
                continue
            for variable in doc.get('variables'):
                variable['duplicate'] = False
                variable['duplicate_group'] = ''
            doc['data_object'] = doc.get('data_object').split('    },')[0] + (
                '    }\r\n]'
            )
        return docs

    def test_ensure_default_api_calls_adds_monitoring_private_zone_call(self):
        db = FakeDb()

        defaults.ensure_default_api_calls(db)

        self.assertEqual(len(db.monitoring.inserted), 1)
        inserted = db.monitoring.inserted[0]
        self.assertEqual(
            inserted.get('title'),
            'Create Private Monitoring Zone'
        )
        self.assertEqual(inserted.get('verb'), 'POST')
        self.assertEqual(
            inserted.get('api_uri'),
            '/v1.0/{ddi}/monitoring_zones'
        )
        self.assertEqual(inserted.get('group'), 'zones')
        self.assertTrue(inserted.get('tested'))
        self.assertTrue(inserted.get('use_data'))

        variable_names = [
            variable.get('variable_name')
            for variable in inserted.get('variables')
        ]
        self.assertEqual(
            variable_names,
            [
                'label',
                'maximum_checks',
                'maximum_agents',
                'disable',
                'metadata_key',
                'metadata_value'
            ]
        )

        request_body = json.loads(inserted.get('data_object'))
        self.assertEqual(request_body.get('label'), '{label}')
        self.assertEqual(
            request_body.get('maximum_checks'),
            '{maximum_checks}'
        )
        self.assertEqual(
            request_body.get('maximum_agents'),
            '{maximum_agents}'
        )
        self.assertEqual(request_body.get('disable'), '{disable}')
        self.assertEqual(
            request_body.get('metadata'),
            {'{metadata_key}': '{metadata_value}'}
        )

    def test_keeps_existing_monitoring_zone_call(self):
        existing = {
            'api_uri': '/v1.0/{ddi}/monitoring_zones',
            'verb': 'POST',
            'title': 'Existing Call'
        }
        db = FakeDb([existing])

        defaults.ensure_default_api_calls(db)

        self.assertEqual(db.monitoring.inserted, [])
        self.assertEqual(db.monitoring.docs, [existing])

    def test_ensure_default_api_calls_adds_rackconnect_groups(self):
        db = FakeDb()

        defaults.ensure_default_api_calls(db)

        settings = db.api_settings.docs[0]
        groups = settings.get('rackconnect').get('groups')
        slugs = [group.get('slug') for group in groups]
        self.assertIn('server_groups', slugs)
        self.assertIn('match_rules', slugs)

    def test_ensure_default_api_calls_adds_missing_rackconnect_calls(self):
        db = FakeDb()

        defaults.ensure_default_api_calls(db)

        calls = db.rack_connect.inserted
        api_calls = [
            (call.get('verb'), call.get('api_uri'), call.get('title'))
            for call in calls
        ]
        self.assertIn(
            (
                'GET',
                '/v3/{ddi}/server_groups',
                'List All Server Groups'
            ),
            api_calls
        )
        self.assertIn(
            (
                'GET',
                '/v3/{ddi}/match_rules/details/{match_rule_id}',
                'List Match Rule By Id'
            ),
            api_calls
        )
        self.assertIn(
            (
                'POST',
                '/v3/{ddi}/load_balancer_pools/nodes',
                'Add Nodes to Load Balancer Pools'
            ),
            api_calls
        )

    def test_does_not_duplicate_rackconnect_call(self):
        existing = {
            'api_uri': '/v3/{ddi}/server_groups',
            'verb': 'GET',
            'title': 'Existing RackConnect Call'
        }
        db = FakeDb(rackconnect_docs=[existing])

        defaults.ensure_default_api_calls(db)

        duplicate_calls = [
            call for call in db.rack_connect.inserted
            if (
                call.get('api_uri') == '/v3/{ddi}/server_groups' and
                call.get('verb') == 'GET'
            )
        ]
        self.assertEqual(duplicate_calls, [])

    def test_updates_existing_rackconnect_bulk_calls(self):
        docs = self.rackconnect_docs_with_pr67_bulk_calls()
        db = FakeDb(rackconnect_docs=docs)

        defaults.ensure_default_api_calls(db)

        self.assertEqual(db.rack_connect.inserted, [])
        self.assertEqual(len(db.rack_connect.updated), 4)
        updated = db.rack_connect.find_one({
            'api_uri': '/v3/{ddi}/load_balancer_pools/nodes',
            'verb': 'POST'
        })
        variables = updated.get('variables')
        self.assertTrue(variables[0].get('duplicate'))
        self.assertEqual(
            variables[0].get('duplicate_group'),
            'load_balancer_pool_nodes'
        )
        self.assertEqual(
            variables[1].get('duplicate_group'),
            'load_balancer_pool_nodes'
        )
        self.assertIn('cloud_server_id_2', updated.get('data_object'))

    def test_updates_only_allowlisted_rackconnect_bulk_calls(self):
        server_groups = {
            'api_uri': '/v3/{ddi}/server_groups',
            'verb': 'GET',
            'title': 'Custom Admin Title',
            'variables': [],
            'data_object': 'custom'
        }
        bulk_call = {
            'api_uri': '/v3/{ddi}/server_groups/nodes',
            'verb': 'DELETE',
            'title': 'Remove Nodes from Server Groups',
            'variables': [
                {
                    'variable_name': 'cloud_server_id',
                    'duplicate': False,
                    'duplicate_group': ''
                }, {
                    'variable_name': 'server_group_id',
                    'duplicate': False,
                    'duplicate_group': ''
                }
            ],
            'data_object': 'custom bulk'
        }
        db = FakeDb(rackconnect_docs=[server_groups, bulk_call])

        defaults.sync_rackconnect_bulk_api_calls(db)

        self.assertEqual(server_groups.get('title'), 'Custom Admin Title')
        self.assertEqual(server_groups.get('data_object'), 'custom')
        self.assertEqual(len(db.rack_connect.updated), 1)
        self.assertEqual(
            bulk_call.get('variables')[0].get('duplicate_group'),
            'server_group_nodes'
        )

    def test_rackconnect_extra_calls_use_pitchfork_endpoint_shape(self):
        for call in defaults.RACKCONNECT_EXTRA_API_CALLS:
            self.assertTrue(call.get('api_uri').startswith('/v3/{ddi}/'))
            self.assertNotIn('{tenant_id}', call.get('api_uri'))
            self.assertNotIn('{tenatn_id}', call.get('api_uri'))

    def test_rackconnect_retain_filter_uses_lowercase_text_value(self):
        calls = [
            call for call in defaults.RACKCONNECT_EXTRA_API_CALLS
            if call.get('api_uri') == (
                '/v3/{ddi}/public_ips?retain={retain_bool}'
            )
        ]

        self.assertEqual(len(calls), 1)
        variable = calls[0].get('variables')[0]
        self.assertEqual(variable.get('field_type'), 'text')
        self.assertEqual(variable.get('field_display_data'), 'true\r\nfalse')

    def test_match_rules_render_cloud_ddi_account_as_integer(self):
        helper = load_helper_module()
        json_data = {
            'cloud_ddi_account': '123456',
            'region': 'dfw',
            'match_rule_id': 'match-rule-id',
            'match_rule_name': 'match-rule',
            'match_criteria_type_id': '1',
            'match_criteria_value': 'criteria',
            'match_action_type_id': '2',
            'match_action_value': 'action',
            'refresh_servers': 'True',
            'cloud_account_match_action_uuid': 'match-action-id'
        }

        calls = [
            call for call in defaults.RACKCONNECT_EXTRA_API_CALLS
            if call.get('title') in [
                'Add Match Rules For Cloud Account',
                'Update Match Rule'
            ]
        ]

        self.assertEqual(len(calls), 2)
        for call in calls:
            data = helper.process_api_data_request(call, json_data)
            if isinstance(data, list):
                data = data[0]
            self.assertEqual(data.get('cloud_ddi_account'), 123456)

    def test_bulk_load_balancer_pool_nodes_render_multiple_rows(self):
        helper = load_helper_module()
        call = next(
            call for call in defaults.RACKCONNECT_EXTRA_API_CALLS
            if call.get('title') == 'Add Nodes to Load Balancer Pools'
        )
        json_data = {
            'cloud_server_id': 'server-0',
            'port': '80',
            'load_balancer_pool_id': 'pool-0',
            'cloud_server_id_1': 'server-1',
            'port_1': '81',
            'load_balancer_pool_id_1': 'pool-1'
        }

        data = helper.process_api_data_request(call, json_data)

        self.assertEqual(len(data), 2)
        self.assertEqual(data[0].get('port'), 80)
        self.assertEqual(data[1].get('port'), 81)
        self.assertEqual(
            data[1].get('cloud_server').get('id'),
            'server-1'
        )
        self.assertEqual(
            data[1].get('load_balancer_pool').get('id'),
            'pool-1'
        )

    def test_bulk_server_group_nodes_render_three_rows(self):
        helper = load_helper_module()
        call = next(
            call for call in defaults.RACKCONNECT_EXTRA_API_CALLS
            if call.get('title') == 'Remove Nodes from Server Groups'
        )
        json_data = {
            'cloud_server_id': 'server-0',
            'server_group_id': 'group-0',
            'cloud_server_id_1': 'server-1',
            'server_group_id_1': 'group-1',
            'cloud_server_id_2': 'server-2',
            'server_group_id_2': 'group-2'
        }

        data = helper.process_api_data_request(call, json_data)

        self.assertEqual(
            [
                item.get('cloud_server').get('id')
                for item in data
            ],
            ['server-0', 'server-1', 'server-2']
        )
        self.assertEqual(
            [
                item.get('server_group').get('id')
                for item in data
            ],
            ['group-0', 'group-1', 'group-2']
        )

    def test_bulk_rackconnect_variables_are_grouped_duplicates(self):
        calls = [
            call for call in defaults.RACKCONNECT_EXTRA_API_CALLS
            if call.get('title') in [
                'Add Nodes to Load Balancer Pools',
                'Remove Nodes from Load Balancer Pools',
                'Add Nodes to Server Groups',
                'Remove Nodes from Server Groups'
            ]
        ]

        self.assertEqual(len(calls), 4)
        for call in calls:
            variables = call.get('variables')
            self.assertTrue(variables[0].get('duplicate'))
            self.assertTrue(variables[0].get('duplicate_group'))
            self.assertTrue(all(
                var.get('duplicate_group') ==
                variables[0].get('duplicate_group')
                for var in variables
            ))

    def test_variable_preserves_duplicate_metadata(self):
        models = load_models_module()

        variable = models.Variable({
            'variable_name': 'cloud_server_id',
            'field_type': 'text',
            'duplicate': True,
            'duplicate_group': 'load_balancer_pool_nodes',
            'id_value': 0
        })

        self.assertTrue(variable.__dict__.get('duplicate'))
        self.assertEqual(
            variable.__dict__.get('duplicate_group'),
            'load_balancer_pool_nodes'
        )


if __name__ == '__main__':
    unittest.main()
