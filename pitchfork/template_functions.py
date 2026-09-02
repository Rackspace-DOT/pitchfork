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


from flask import g
from models import Product
from bson.objectid import ObjectId


import re


def utility_processor():
    def unslug(string):
        return re.sub('_|\+', ' ', string)

    def parse_field_data(value):
        choices = re.sub('\r\n', ',', value)
        return choices.split(',')

    def duplicate_row_limit(api_call, variable_name):
        """Highest row index this call's request body can actually hold.

        The duplicate-row UI names cloned inputs <variable>_1, <variable>_2,
        and so on, but process_api_data_request only fills placeholders that
        already exist in data_object -- a row with no matching placeholder is
        silently dropped from the request. So the body is what really caps the
        rows, and reading the cap back off it keeps the two from drifting
        apart. A body carrying {x} ... {x_9} returns 9, allowing ten rows.

        Returns 0 when nothing can be cloned, which is the signal not to offer
        the controls at all.
        """
        if not variable_name:
            return 0

        data_object = api_call.get('data_object') or ''
        suffixes = re.findall(
            r'\{%s_(\d+)\}' % re.escape(variable_name),
            data_object
        )
        if not suffixes:
            return 0

        return max(int(suffix) for suffix in suffixes)

    def slugify(data):
        temp_string = re.sub(' +', ' ', str(data.strip()))
        return re.sub(' ', '_', temp_string)

    def get_product_for_call(product):
        temp_product = g.db.api_settings.find_one()
        if temp_product and temp_product.get(product):
            return Product(temp_product.get(product))
        return {}

    def get_product_call(call_id, db_name):
        if call_id:
            return getattr(g.db, db_name).find_one(
                {'_id': ObjectId(str(call_id))}
            )

    return dict(
        parse_field_data=parse_field_data,
        duplicate_row_limit=duplicate_row_limit,
        unslug=unslug,
        slugify=slugify,
        get_product_for_call=get_product_for_call,
        get_product_call=get_product_call
    )
