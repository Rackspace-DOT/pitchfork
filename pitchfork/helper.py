# Copyright 2014 Dave Kludt
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from pygments.lexers import JsonLexer, XmlLexer
from pygments.formatters import HtmlFormatter
from bson.objectid import ObjectId
from pygments import highlight
from flask import g, session
from models import Variable
from dateutil import tz
from url_safety import (
    UnsafeOutboundRequest,
    _build_api_url,
    _validate_endpoint_hostname,
    _validate_outbound_url,
    sanitize_query_filter,
)


import re
import copy
import requests
import json
import forms
import pymongo
import datetime


UTC = tz.tzutc()
API_REQUEST_TIMEOUT = (3.05, 30)


class InvalidRequestData(Exception):
    pass


def cast_request_value(value, var_type, variable_name):
    try:
        if var_type == 'integer':
            return int(value.strip())
        if var_type == 'float':
            return float(value.strip())
    except (TypeError, ValueError):
        raise InvalidRequestData(
            'Invalid %s value for %s.' % (var_type, variable_name)
        )
    return None


requests.packages.urllib3.disable_warnings()


def get_timestamp():
    return datetime.datetime.now(UTC)


def check_for_product_regions(product=None):
    restrict_regions, regions = False, []
    api_settings = g.db.api_settings.find_one()
    if api_settings:
        regions = api_settings.get('regions')
        if not regions:
            regions = api_settings.get('dcs')

        if product and product.require_region:
            restrict_regions = check_url_endpoints(
                product.us_api,
                product.uk_api
            )
            if restrict_regions:
                regions = [
                    {'abbreviation': 'US'},
                    {'abbreviation': 'UK'}
                ]

    return restrict_regions, regions


def generate_group_choices(product):
    choices = [('', '')]
    for group in product.groups:
        choices.append((group.get('slug'), group.get('name')))
    choices.append(('add_new_group', 'Add New Group'))
    return choices


def check_url_endpoints(us, uk):
    us_find = re.findall(r'\{(.+?)\}', us)
    uk_find = re.findall(r'\{(.+?)\}', uk)
    if 'region' in us_find:
        return False

    if 'region' in uk_find:
        return False

    return True


def change_group_order(groups, new_position, old_position, group_slug, db):
    for group in groups:
        if group.get('order') == new_position:
            g.db.api_settings.update(
                {
                    '%s.groups.slug' % db: group.get('slug')
                }, {
                    '$set': {
                        '%s.groups.$.order' % db: old_position
                    }
                }
            )
        elif group.get('slug') == group_slug:
            g.db.api_settings.update(
                {
                    '%s.groups.slug' % db: group.get('slug')
                }, {
                    '$set': {
                        '%s.groups.$.order' % db: new_position
                    }
                }
            )
    return


def gather_api_calls(product, testing, groups):
    if testing:
        query = {
            '$or': [
                {'tested': False},
                {'tested': None}
            ]
        }
    else:
        query = {'tested': True}

    api_calls = getattr(g.db, product.get_db_name()).find(query).sort(
        [('title', pymongo.ASCENDING)]
    )
    calls = {'': []}
    for group in groups:
        calls[group.get('slug')] = []

    for call in api_calls:
        if call.get('group'):
            calls[call.get('group')].append(call)
        else:
            calls[''].append(call)

    return calls


def add_fields_to_form(count):
    class F(forms.ApiCall):
        pass

    for i in range(count):
        setattr(
            F,
            'variable_%i' % i,
            forms.fields.FormField(forms.CallVariables)
        )

    setattr(
        F,
        'submit',
        forms.fields.SubmitField('Submit')
    )
    return F()


def generate_edit_call_form(product, call, call_id):
    count = 1
    if len(call.variables) > 1:
        count = len(call.variables)

    form = add_fields_to_form(count)
    if len(call.variables) > 0:
        for i in range(count):
            temp = getattr(form, 'variable_%i' % i)
            temp_variable = Variable(call.variables[i])
            temp.form.field_type.data = temp_variable.field_type
            temp.form.required.data = temp_variable.required
            temp.form.description.data = temp_variable.description
            temp.form.variable_name.data = temp_variable.variable_name
            temp.form.field_display.data = temp_variable.field_display
            temp.form.duplicate.data = temp_variable.duplicate
            temp.form.duplicate_group.data = temp_variable.duplicate_group
            temp.form.field_display_data.data = (
                temp_variable.field_display_data
            )
            temp.form.id_value.data = temp_variable.id_value

    for key, value in call.__dict__.iteritems():
        if key != 'variables':
            setattr(getattr(form, key), 'data', value)

    form.id.data = call_id
    return form, count


def get_vars_for_call(submissions):
    data, count = [], []
    for key, value in submissions:
        temp = re.search(r'variable_(\d+?)-(\w.*)', key)
        if temp:
            if count:
                if not int(temp.group(1)) in count:
                    count.append(int(temp.group(1)))
            else:
                count.append(int(temp.group(1)))

    for placeholder in range(len(count)):
        data.append({'ignore': 'placeholder'})

    for key, value in submissions:
        temp = re.search(r'variable_(\d+?)-(\w.*)', key)
        if temp:
            if temp.group(2) != 'csrf_token':
                if str(temp.group(2)) == 'required':
                    if value[0]:
                        data[int(temp.group(1))][str(temp.group(2))] = bool(
                            value[0]
                        )
                elif str(temp.group(2)) == 'id_value':
                    if value[0]:
                        data[int(temp.group(1))][str(temp.group(2))] = int(
                            value[0]
                        )
                else:
                    data[int(temp.group(1))][str(temp.group(2))] = value[0]

    return_data = []
    for variable in data:
        if variable.get('variable_name'):
            temp_var = Variable(variable)
            return_data.append(temp_var.__dict__)

    return return_data


def generate_vars_for_call(product, call, request):
    data_package = None
    api_url = generate_api_url_for_call(product, request)
    if call.get('use_data'):
        if request.json.get('mock'):
            data_package = json.loads(call.get('data_object'))
        else:
            """
                Find escaped newlines and replace them with a non escaped
                newline in the raw string. Doing this in order to handle JSON
                escaped newlines from the request form so that it can be
                encoded again for the API request correctly
            """
            temp_data = re.sub(r'\\\\n', r'\\n', request.data)
            data_request = json.loads(temp_data)
            data_package = process_api_data_request(call, data_request)

    header = create_custom_header(call, request.json)
    return api_url, header, data_package


def generate_api_url_for_call(product, request):
    data_center = request.json.get('data_center')
    if data_center in ['uk', 'lon']:
        region_url = product.uk_api
    else:
        region_url = product.us_api

    is_mock = bool(request.json.get('mock'))
    temp_url = _build_api_url(region_url, request.json.get('api_url'))
    if request.json.get('mock'):
        if data_center is None:
            api_url = temp_url
        else:
            api_url = process_api_url(temp_url, request)
    else:
        api_url = process_api_url(temp_url, request)

    if is_mock:
        return api_url

    _validate_endpoint_hostname(region_url, api_url)
    return _validate_outbound_url(api_url)


def process_api_url(url, request):
    """ Regex loop to replace the URL with the needed values """

    def evaluate_replace(m):
        replacement = request.json.get(m.group(2))
        if not replacement and m.group(2) == 'region':
            replacement = request.json.get('data_center')
        if replacement:
            return re.sub(
                m.group(1),
                replacement.strip(),
                m.group(0)
            )
        else:
            return m.group(1)

    api_url = re.sub(r'(\{(.+?)\})', evaluate_replace, url)
    temp_filter = request.json.get('add_filter')
    if temp_filter and len(temp_filter) > 1:
        temp_filter = sanitize_query_filter(temp_filter)
        if temp_filter:
            api_url = '%s?%s' % (api_url, temp_filter)

    return api_url


def check_variable_type(api_call, key_value):
    for var in api_call.get('variables'):
        if var.get('variable_name') == key_value:
            return var.get('field_type')

    base_key = re.match(r'(.+)_\d+$', key_value)
    if base_key:
        for var in api_call.get('variables'):
            if var.get('variable_name') == base_key.group(1):
                return var.get('field_type')

    return 'string'


def variable_for_placeholder(api_call, name):
    for var in api_call.get('variables') or []:
        if var.get('variable_name') == name:
            return var

    base_name = re.match(r'(.+)_\d+$', name)
    if base_name:
        for var in api_call.get('variables') or []:
            if var.get('variable_name') == base_name.group(1):
                return var

    return {}


def is_duplicate_group_placeholder(api_call, name):
    return bool(variable_for_placeholder(api_call, name).get(
        'duplicate_group'
    ))


def is_required_duplicate_group_placeholder(api_call, name):
    variable = variable_for_placeholder(api_call, name)
    return variable.get('duplicate_group') and variable.get('required')


DUPLICATE_ROW_COMPLETE = 'complete'
DUPLICATE_ROW_BLANK = 'blank'
DUPLICATE_ROW_PARTIAL = 'partial'


def duplicate_row_state(api_call, json_data, item):
    """Classify a duplicate row and name the required values it is missing.

    The stored body always renders every row slot, so a user filling three
    rows leaves the rest untouched: a row where no grouped field at all was
    filled is blank and gets dropped. Once any grouped field carries a value
    the user meant to send that row, so a missing required value there is a
    mistake -- dropping it silently would return success for a request that
    quietly lost data -- and callers reject the whole submission instead.

    Optional grouped fields count toward "the user filled something in", which
    is why blankness is judged over every grouped placeholder rather than only
    the required ones.
    """
    placeholders = re.findall(r'\{([^{}]+?)\}', json.dumps(item))
    grouped = [
        name for name in placeholders
        if is_duplicate_group_placeholder(api_call, name)
    ]
    if not grouped:
        return DUPLICATE_ROW_COMPLETE, []

    missing_required = [
        name for name in grouped
        if is_required_duplicate_group_placeholder(api_call, name) and
        not json_data.get(name)
    ]
    if not any(json_data.get(name) for name in grouped):
        return DUPLICATE_ROW_BLANK, missing_required

    if missing_required:
        return DUPLICATE_ROW_PARTIAL, missing_required

    return DUPLICATE_ROW_COMPLETE, []


def incomplete_duplicate_row_message(missing_values):
    """Name the fields keeping a partly filled duplicate row from sending."""
    return 'Missing required duplicate row values: %s.' % ', '.join(
        missing_values
    )


def recursive_dict_object(
    parent_key,
    value,
    api_call,
    json_data,
    data_object,
    temp_dict,
    req_key,
    req_key_value
):
    if isinstance(value, dict):
        sub_dict = {}
        temp_parent = parent_key
        for sub_key, sub_value in value.iteritems():
            sub_dict = recursive_dict_object(
                sub_key,
                sub_value,
                api_call,
                json_data,
                data_object,
                sub_dict,
                req_key,
                req_key_value
            )

        if sub_dict:
            temp_dict[parent_key] = sub_dict
        else:
            if temp_parent == req_key:
                temp_dict[parent_key] = req_key_value

    elif isinstance(value, list):
        temp_list = []
        sub_list = []
        skipped_required_duplicate_row = False
        for value_list in value:
            if isinstance(value_list, dict):
                row_state, missing_values = duplicate_row_state(
                    api_call,
                    json_data,
                    value_list
                )
                if row_state == DUPLICATE_ROW_PARTIAL:
                    raise InvalidRequestData(
                        incomplete_duplicate_row_message(missing_values)
                    )
                if row_state == DUPLICATE_ROW_BLANK:
                    skipped_required_duplicate_row = True
                    continue
                temp_list_dict = {}
                for sub_dict_key, sub_dict_value in value_list.iteritems():
                    temp_list_dict = recursive_dict_object(
                        sub_dict_key,
                        sub_dict_value,
                        api_call,
                        json_data,
                        data_object,
                        temp_list_dict,
                        req_key,
                        req_key_value
                    )

                if len(temp_list_dict) > 0:
                    temp_list.append(copy.deepcopy(temp_list_dict))

            else:
                _key = re.match(r'\{(.+?)\}', value_list)
                if _key:
                    _value = json_data.get(_key.group(1))
                    if _value and _value != '':
                        var_type = check_variable_type(
                            api_call,
                            _key.group(1)
                        )
                        if var_type == 'integer':
                            sub_list.append(cast_request_value(
                                _value,
                                var_type,
                                _key.group(1)
                            ))
                        elif var_type == 'float':
                            sub_list.append(cast_request_value(
                                _value,
                                var_type,
                                _key.group(1)
                            ))
                        elif var_type == 'boolean':
                            if _value.lower() == 'false':
                                _value = ''
                            sub_list.append(bool(_value.strip()))
                        elif var_type == 'list':
                            _temp_store = []
                            for item in _value.strip().split(','):
                                _temp_store.append(item.strip())

                            sub_list.append(_temp_store)
                        elif var_type == 'text/integer':
                            try:
                                sub_list.append(int(_value.strip()))
                            except Exception:
                                sub_list.append(_value.strip())
                        else:
                            sub_list.append(_value.strip())

        if sub_list:
            temp_dict[str(parent_key)] = sub_list

        if temp_list:
            temp_dict[str(parent_key)] = temp_list
        elif skipped_required_duplicate_row and not sub_list:
            raise InvalidRequestData(
                'At least one complete duplicate row is required.'
            )

    else:
        if value:
            _pkey = re.match(r'\{(.+?)\}', parent_key)
            if _pkey:
                _pkey_value = json_data.get(_pkey.group(1))
                if not _pkey_value:
                    return temp_dict
            else:
                _pkey_value = parent_key

            try:
                _key = re.match(r'\{(.+?)\}', value)
            except Exception:
                _key = None

            if _key:
                _value = json_data.get(_key.group(1))
                if _value and _value != '':
                    var_type = check_variable_type(
                        api_call,
                        _key.group(1)
                    )
                    if _value != "null":
                        if var_type == 'integer':
                            temp_dict[str(_pkey_value)] = cast_request_value(
                                _value,
                                var_type,
                                _key.group(1)
                            )
                        elif var_type == 'float':
                            temp_dict[str(_pkey_value)] = cast_request_value(
                                _value,
                                var_type,
                                _key.group(1)
                            )
                        elif var_type == 'boolean':
                            if _value.lower() == 'false':
                                _value = ''
                            temp_dict[str(_pkey_value)] = bool(_value.strip())
                        elif var_type == 'list':
                            _temp_store = []
                            for item in _value.strip().split(','):
                                _temp_store.append(item.strip())

                            temp_dict[str(_pkey_value)] = _temp_store
                        elif var_type == 'text/integer':
                            try:
                                temp_dict[str(_pkey_value)] = int(
                                    _value.strip()
                                )
                            except Exception:
                                temp_dict[str(_pkey_value)] = _value.strip()
                        else:
                            temp_dict[str(_pkey_value)] = _value.strip()
                    else:
                        temp_dict[str(_pkey_value)] = None
            else:
                temp_dict[str(parent_key)] = value

        if value == 'none' or value is None:
            temp_dict[str(_pkey_value)] = value

    return temp_dict


def process_api_data_request(api_call, json_data):
    data_object = api_call.get('data_object')

    """ Setup the data structure with the appropriate values """
    temp_json = json.loads(data_object)
    temp_dict = {}
    temp_list = []
    req_key = None
    req_key_value = None

    def evaluate_replace(m):
        if json_data.get(m.group(2)):
            return json_data.get(m.group(2)).strip()
        else:
            return m.group(1)

    if api_call.get('required_key'):
        req_key = api_call.get('required_key_name')
        if api_call.get('required_key_type') == 'dict':
            req_key_value = {}
        elif api_call.get('required_key_type') == 'list':
            req_key_value = []

    if isinstance(temp_json, dict):
        for key, value in temp_json.iteritems():
            if value:
                temp_dict = recursive_dict_object(
                    key,
                    value,
                    api_call,
                    json_data,
                    temp_json,
                    temp_dict,
                    req_key,
                    req_key_value
                )
            if value is None:
                temp_dict[str(key)] = None

        return temp_dict

    elif isinstance(temp_json, list):
        skipped_required_duplicate_row = False
        for item in temp_json:
            if isinstance(item, dict):
                row_state, missing_values = duplicate_row_state(
                    api_call,
                    json_data,
                    item
                )
                if row_state == DUPLICATE_ROW_PARTIAL:
                    raise InvalidRequestData(
                        incomplete_duplicate_row_message(missing_values)
                    )
                if row_state == DUPLICATE_ROW_BLANK:
                    skipped_required_duplicate_row = True
                    continue
                temp_item_dict = {}
                for key, value in item.iteritems():
                    if value:
                        temp_item_dict = recursive_dict_object(
                            key,
                            value,
                            api_call,
                            json_data,
                            temp_json,
                            temp_item_dict,
                            req_key,
                            req_key_value
                        )
                    if value is None:
                        temp_item_dict[str(key)] = None

                if temp_item_dict:
                    temp_list.append(temp_item_dict)
            else:
                value = re.sub(r'(\{(.+?)\})', evaluate_replace, item)
                temp_list.append(value)

        if not temp_list and skipped_required_duplicate_row:
            raise InvalidRequestData(
                'At least one complete duplicate row is required.'
            )

        return temp_list
    elif isinstance(temp_json, basestring):
        missing_value = [False]

        def evaluate_scalar_replace(m):
            if json_data.get(m.group(1)):
                return json_data.get(m.group(1)).strip()
            missing_value[0] = True
            return ''

        value = re.sub(r'\{([^{}]+?)\}', evaluate_scalar_replace, temp_json)
        if missing_value[0]:
            return None
        return value
    else:
        return temp_json


def create_custom_header(api_call, request):
    def evaluate_replace(m):
        return re.sub(m.group(1), request.get(m.group(2)), m.group(0))

    header = {}
    if not api_call.get('remove_content_type'):
        header['Content-Type'] = 'application/json'

    if not api_call.get('remove_token'):
        header['X-Auth-Token'] = request.get('api_token')

    if request.get('mock') and not api_call.get('remove_token'):
        header['X-Auth-Token'] = '{api-token}'

    if (
        api_call.get('change_content_type') and
        api_call.get('custom_content_type') is not None
    ):
        header['Content-Type'] = api_call.get('custom_content_type')

    if api_call.get('add_to_header'):
        temp_value = api_call.get('custom_header_value')
        key_value = re.sub(r'(\{(.+?)\})', evaluate_replace, temp_value)
        header[api_call.get('custom_header_key')] = key_value.strip()

    return header


def process_api_request(url, verb, data, headers, html_convert=True):
    try:
        _validate_outbound_url(
            url,
            require_https=bool(headers and headers.get('X-Auth-Token'))
        )
        request_method = getattr(requests, verb.lower())
        if data:
            response = request_method(
                url,
                headers=headers,
                data=json.dumps(data),
                verify=False,
                allow_redirects=False,
                timeout=API_REQUEST_TIMEOUT
            )
        else:
            response = request_method(
                url,
                headers=headers,
                verify=False,
                allow_redirects=False,
                timeout=API_REQUEST_TIMEOUT
            )
    except Exception as e:
        return (
            headers,
            (
                "<span class='error-response'>An error occured "
                "with the request. Details are below</span>"
            ),
            str(e), ''
        )

    try:
        response_headers = json.loads(response.headers)
    except Exception:
        response_headers = dict(response.headers)

    try:
        content_type = response_headers.get('content-type').split(';')
    except Exception:
        content_type = []

    try:
        if (
            'application/xml' in content_type or
            'application/atom+xml' in content_type
        ):
            if html_convert:
                content = pretty_format_data(response.content, True)
            else:
                content = response.content
        else:
            if html_convert:
                content = pretty_format_data(json.loads(response.content))
            else:
                content = json.loads(response.content)
    except Exception:
        temp = re.findall(r'<body>(.+?)<\/body>', response.content, re.S)
        if temp:
            formatted_content = re.sub(
                r'\n|\r|\s\s+?|<br \/>|<h1>',
                '',
                temp[0]
            )
            content = re.sub(r'<\/h1>', '<br />', formatted_content)
        elif len(response.text) > 5:
            content = "%s Status Code: %s" % (
                str(response.text),
                str(response.status_code)
            )
        else:
            content = "No content recieved. Status Code: %s" % str(
                response.status_code
            )

    if html_convert:
        headers = pretty_format_data(headers)
        response_headers = pretty_format_data(response_headers)

    return headers, response_headers, content, response.status_code


def pretty_format_data(data, content_type=False):
    if data:
        if content_type:
            return highlight(data, XmlLexer(), HtmlFormatter())
        else:
            return highlight(
                json.dumps(data, indent=4),
                JsonLexer(),
                HtmlFormatter()
            )


def pretty_format_url(url):
    if url:
        temp_url = '<pre><span class="nt">%s</span></pre>' % url
        temp_url = re.sub('{', '<span class="s2">{', temp_url)
        temp_url = re.sub('}', '}</span>', temp_url)
        return temp_url


def log_api_call_request(
    req_headers,
    rep_headers,
    rep_body,
    rep_code,
    call,
    request,
    data_package,
    api_url,
    title
):
    if not request.get('api_verb') in ['PUT', 'POST', 'DELETE']:
        rep_body = None

    if data_package:
        data_package = sanitize_keys_for_mongo(data_package)

    try:
        g.db.history.insert(
            {
                'response': {
                    'code': rep_code,
                    'headers': rep_headers,
                    'body': rep_body
                },
                'request': {
                    'verb': request.get('api_verb'),
                    'url': api_url,
                    'data': data_package
                },
                'details': {
                    'id': call.get('_id'),
                    'title': call.get('title'),
                    'description': call.get('short_description'),
                    'doc_url': call.get('doc_url')
                },
                'ddi': request.get('ddi'),
                'data_center': request.get('region'),
                'username': session.get('username'),
                'completed_at': get_timestamp(),
                'product': title
            }
        )
    except Exception:
        pass

    return


def sanitize_keys_for_mongo(value):
    """Escape dots in dict keys so the history record can be stored.

    Mongo rejects dots in document keys, and a body that builds its keys from
    user input -- a metadata key, say -- can produce them. Only keys need
    escaping here: the body itself was already built for the outbound call.
    """
    if isinstance(value, dict):
        return {
            re.sub(r'\.', '&#46;', str(key)): sanitize_keys_for_mongo(item)
            for key, item in value.iteritems()
        }

    if isinstance(value, list):
        return [sanitize_keys_for_mongo(item) for item in value]

    return value


def sanitize_data_for_mongo(data):
    temp_dict = {}
    for k, v in data.iteritems():
        if type(v) is not list:
            temp_dict[k] = re.sub(r'\.', '&#46;', v)
        else:
            temp_dict[k] = v

    return temp_dict


def gather_history():
    history = []
    history = g.db.history.find(
        {
            'username': session.get('username')
        }
    ).sort('completed_at', pymongo.DESCENDING).limit(100)

    return history


def gather_favorites(only_ids=False):
    favorites = []
    user_favorites = g.db.favorites.find_one(
        {
            'username': session.get('username')
        }
    )
    if user_favorites:
        for call in user_favorites.get('favorites'):
            query = {'_id': ObjectId(call.get('call_id'))}
            temp_call = getattr(g.db, call.get('db_name')).find_one(query)
            if temp_call:
                if only_ids:
                    favorites.append(call.get('call_id'))
                else:
                    temp_call['product-db_name'] = call.get('db_name')
                    temp_call['product-app_url'] = call.get('app_url')
                    favorites.append(temp_call)

    return favorites
