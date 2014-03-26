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

from flask import Blueprint, current_app, render_template, g
from pitchfork.adminbp.decorators import check_perms


bp = Blueprint(
    'networks',
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/networks'
)


COLLECTION = 'cloud_networks'
BLUEPRINT = 'networks'
URL_LINK = 'networks'
TITLE = 'Cloud Networks'


import pymongo
import global_routes
