/*
 * Regression tests for the duplicate-row cloning JS in
 * pitchfork/templates/product_front.html.
 *
 * These exist because PR #68 shipped a bug that no Python test could see:
 * update_duplicate_field() looked up its input with ':input:visible' on a
 * DETACHED clone. Detached elements are never :visible in any browser, so the
 * lookup came back empty, attr('name') was undefined, and the rename threw --
 * after the source '+' had already been hidden. The user saw the button vanish
 * with no new rows.
 *
 * The JS is read out of the template itself rather than copied here, so these
 * tests exercise the shipped code. Run via tests/test_duplicate_rows_js.py, or
 * directly:
 *
 *     cd tests/js && npm install && node duplicate_rows.test.js
 */

'use strict';

var fs = require('fs');
var path = require('path');
var {JSDOM} = require('jsdom');

var TEMPLATE = path.join(
    __dirname, '..', '..', 'pitchfork', 'templates', 'product_front.html'
);
var JQUERY = require.resolve('jquery/dist/jquery.js');

/* Every helper the tests drive. If a refactor moves one of these out of the
 * template, fail loudly rather than quietly testing an empty string. */
var REQUIRED = [
    'function duplicate_field_count',
    'function duplicate_field_name',
    'function duplicate_group_rows',
    'function duplicate_group_indexes',
    'function duplicate_group_max',
    'function refresh_duplicate_group',
    'function update_duplicate_field',
    'function add_duplicate_group',
    'function remove_duplicate_group',
    '.duplicate-field',
    '.remove-field'
];


function extractDuplicateScript() {
    var html = fs.readFileSync(TEMPLATE, 'utf8');
    var blocks = html.match(/<script\b[^>]*>[\s\S]*?<\/script>/g) || [];
    var block = null;
    for (var i = 0; i < blocks.length; i++) {
        if (blocks[i].indexOf('function duplicate_field_count') !== -1) {
            block = blocks[i];
            break;
        }
    }
    if (!block) {
        throw new Error(
            'no <script> block in product_front.html defines ' +
            'duplicate_field_count -- update this extractor'
        );
    }

    var body = block
        .replace(/^<script\b[^>]*>/, '')
        .replace(/<\/script>$/, '');

    /* The top of this block is Jinja (endpoints, require_region, ...) and it
     * calls into ui.js on document.ready. Take only the duplicate-row region,
     * which is plain JS. */
    var start = body.indexOf('function duplicate_field_count');
    var source = body.slice(start);

    if (/\{\{|\{%/.test(source)) {
        throw new Error(
            'the duplicate-row JS now contains Jinja syntax; this extractor ' +
            'can no longer eval it directly -- update the test'
        );
    }
    REQUIRED.forEach(function(needle) {
        if (source.indexOf(needle) === -1) {
            throw new Error(
                'expected to find "' + needle + '" in the duplicate-row JS; ' +
                'the template changed shape -- update the test'
            );
        }
    });
    return source;
}


var DUPLICATE_JS = extractDuplicateScript();


function load(bodyHtml) {
    var dom = new JSDOM(
        '<!doctype html><html><body>' + bodyHtml + '</body></html>',
        {runScripts: 'dangerously'}
    );
    var window = dom.window;
    window.eval(fs.readFileSync(JQUERY, 'utf8'));

    /* Bootstrap is not loaded here. */
    window.eval('jQuery.fn.tooltip = function(){ return this; };');
    window.eval('jQuery.fn.popover = function(){ return this; };');

    /* jsdom performs no layout, so offsetWidth/offsetHeight are always 0 and
     * jQuery's stock :visible reports EVERY element hidden -- which would make
     * these tests fail for the wrong reason and would also mask the very bug
     * they cover. Model the part of real :visible that matters here: an
     * element is visible when it is attached to the document and not
     * display:none. Detached nodes (a fresh .clone()) stay invisible, exactly
     * as in a browser. */
    window.eval(
        'jQuery.expr.pseudos.visible = function(el) {' +
        '  return !!(el.ownerDocument' +
        '            && el.ownerDocument.contains(el)' +
        '            && (el.style ? el.style.display !== "none" : true));' +
        '};'
    );

    /* The real page binds this handler on load, before any cloning. */
    window.eval('(function(){ ' + DUPLICATE_JS + ' })();');
    return window;
}


/* Mirrors the variables table in _call_layout.html. Only rows carrying a
 * duplicate_group get the row class/attribute; only the first variable of a
 * group renders the '+', and only grouped rows render the '-'.
 *
 * maxIndex is the highest row suffix the call's request body can hold, which
 * the template derives from data_object via duplicate_row_limit(). 2 means
 * three rows, 9 means ten. */
function variablesTable(rows, maxIndex) {
    if (maxIndex === undefined) { maxIndex = 2; }
    var html = rows.map(function(row) {
        var isGroup = !!row.group;
        /* buttonGroup lets a test point the controls at a group the rows do
         * not belong to. */
        var buttonGroup = row.buttonGroup !== undefined
            ? row.buttonGroup
            : (row.group || '');
        var controls = '';
        if (row.plus) {
            controls =
                '<a class="duplicate-field tooltip-title" title="Add Row"' +
                ' data-duplicate-group="' + buttonGroup + '"' +
                ' data-duplicate-max="' + maxIndex + '"></a>';
            if (isGroup) {
                controls +=
                    '<a class="remove-field tooltip-title"' +
                    ' title="Remove Row"' +
                    ' data-duplicate-group="' + buttonGroup + '"' +
                    ' style="display: none;"></a>';
            }
        }
        var field = row.select
            ? '<select id="' + row.name + '" name="' + row.name + '">' +
              '<option value="">Choose</option>' +
              '<option value="original" selected>Original</option>' +
              '</select>'
            : '<input id="' + row.name + '" name="' + row.name +
              '" type="text">';
        return '<tr' + (isGroup
                ? ' class="duplicate-group-row" data-duplicate-group="' +
                  row.group + '"'
                : '') + '>' +
            '<td>' + controls + '</td>' +
            '<td class="variable-name-cell">' + row.name + '</td>' +
            '<td>' + field + '</td>' +
            '</tr>';
    }).join('');
    return '<table id="t"><tbody>' + html + '</tbody></table>';
}

/* "Add Nodes to Load Balancer Pools" -- two variables, one group. */
function loadBalancerPoolRows() {
    return [
        {name: 'cloud_server_id', group: 'load_balancer_pool_nodes',
         plus: true},
        {name: 'load_balancer_pool_id', group: 'load_balancer_pool_nodes'}
    ];
}

/* The shipped bulk bodies hold ten association rows. */
var BULK_MAX_INDEX = 9;


function inputNames(window) {
    return window.eval(
        'jQuery("#t :input").map(function(){' +
        ' return jQuery(this).attr("name"); }).get().join(",")'
    ).split(',').filter(Boolean);
}

function rowLabels(window) {
    return window.eval(
        'jQuery("#t .variable-name-cell").map(function(){' +
        ' return jQuery(this).text().trim(); }).get().join(",")'
    ).split(',').filter(Boolean);
}

function rowCount(window) {
    return window.eval('jQuery("#t tr").length');
}

function visiblePlusCount(window) {
    return window.eval(
        'jQuery(".duplicate-field").filter(function(){' +
        ' return this.style.display !== "none"; }).length'
    );
}

function visibleRemoveCount(window) {
    return window.eval(
        'jQuery(".remove-field").filter(function(){' +
        ' return this.style.display !== "none"; }).length'
    );
}

/* Click the first still-visible control of `selector`. Returns 'ok', 'none'
 * when there is nothing left to click, or 'threw: ...'. A throw is the PR #68
 * failure. */
function clickControl(window, selector) {
    return window.eval(
        '(function(){' +
        '  var b = jQuery("' + selector + '").filter(function(){' +
        '    return this.style.display !== "none"; }).first();' +
        '  if (!b.length) { return "none"; }' +
        '  try { b.trigger("click"); return "ok"; }' +
        '  catch (e) { return "threw: " + e.name + ": " + e.message; }' +
        '})()'
    );
}

function clickPlus(window) {
    return clickControl(window, '.duplicate-field');
}

function clickMinus(window) {
    return clickControl(window, '.remove-field');
}

/* Trigger a control even when it is hidden. The anchors stay in the DOM and
 * clone(true) copies their handlers, so a click can still arrive on one the
 * refresh has hidden; the cap has to hold on its own. */
function forceClick(window, selector) {
    return window.eval(
        '(function(){' +
        '  var b = jQuery("' + selector + '").last();' +
        '  if (!b.length) { return "none"; }' +
        '  try { b.trigger("click"); return "ok"; }' +
        '  catch (e) { return "threw: " + e.name + ": " + e.message; }' +
        '})()'
    );
}

/* The PR #68 symptom, as an invariant: a click may never consume the '+'
 * without producing rows. */
function clickAndAssertProgress(window, label) {
    var before = rowCount(window);
    var plusBefore = visiblePlusCount(window);
    var result = clickPlus(window);

    assert.equal(result, 'ok', label + ': click should not throw');
    if (rowCount(window) === before) {
        assert.ok(
            visiblePlusCount(window) >= plusBefore,
            label + ': click added no rows but consumed the "+" ' +
            '(PR #68 regression)'
        );
    }
    assert.ok(
        rowCount(window) > before,
        label + ': click should add rows (had ' + before + ')'
    );
}


/* --- tiny assertion + runner, so this needs no test framework ------------ */

var failures = [];
var current = null;

var assert = {
    ok: function(value, message) {
        if (!value) { throw new Error(message || 'expected truthy'); }
    },
    equal: function(actual, expected, message) {
        if (actual !== expected) {
            throw new Error(
                (message || 'values differ') +
                ' (expected ' + JSON.stringify(expected) +
                ', got ' + JSON.stringify(actual) + ')'
            );
        }
    },
    deepEqual: function(actual, expected, message) {
        var a = JSON.stringify(actual);
        var e = JSON.stringify(expected);
        if (a !== e) {
            throw new Error(
                (message || 'values differ') +
                ' (expected ' + e + ', got ' + a + ')'
            );
        }
    }
};

function test(name, fn) {
    current = name;
    try {
        fn();
        console.log('ok   - ' + name);
    } catch (err) {
        failures.push(name + ': ' + err.message);
        console.log('FAIL - ' + name);
        console.log('       ' + err.message);
    }
    current = null;
}


/* --- tests -------------------------------------------------------------- */

test('grouped: one click clones every row in the group', function() {
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );

    clickAndAssertProgress(window, 'first click');

    assert.equal(rowCount(window), 4, 'two base rows plus two clones');
    assert.deepEqual(
        inputNames(window),
        ['cloud_server_id', 'load_balancer_pool_id',
         'cloud_server_id_1', 'load_balancer_pool_id_1'],
        'clone inputs are suffixed _1'
    );
    assert.deepEqual(
        rowLabels(window),
        ['cloud_server_id', 'load_balancer_pool_id',
         'cloud_server_id_1', 'load_balancer_pool_id_1'],
        'displayed parameter names track the input names'
    );
    assert.equal(
        visiblePlusCount(window), 1,
        'the source "+" is spent and the clone offers the next one'
    );
});

test('grouped: numeric-looking group names clone normally', function() {
    var window = load(variablesTable([
        {name: 'cloud_server_id', group: '42', plus: true},
        {name: 'port', group: '42'}
    ], BULK_MAX_INDEX));

    clickAndAssertProgress(window, 'numeric group click');

    assert.deepEqual(
        inputNames(window),
        ['cloud_server_id', 'port', 'cloud_server_id_1', 'port_1'],
        'numeric group names must not be type-coerced by jQuery.data()'
    );
});

test('grouped: cloned select fields remain serializable', function() {
    var window = load(variablesTable([
        {name: 'cloud_server_id', group: 'nodes', plus: true},
        {name: 'port', group: 'nodes', select: true}
    ], BULK_MAX_INDEX));

    clickAndAssertProgress(window, 'select clone click');

    assert.equal(
        window.eval(
            'jQuery("#t select[name=port_1]").prop("selectedIndex")'
        ),
        0,
        'cloned select should reset to its placeholder'
    );
    assert.ok(
        window.eval('jQuery("#t :input").serialize()')
            .indexOf('port_1=') !== -1,
        'cloned select should be present in serialize()'
    );
});

test('grouped: cloned tooltips do not share source tooltip data', function() {
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );
    window.eval(
        'jQuery(".duplicate-field").first()' +
        '.data("bs.tooltip", {$element: "source"})'
    );

    clickAndAssertProgress(window, 'tooltip clone click');

    assert.equal(
        window.eval(
            'jQuery(".duplicate-field").last().data("bs.tooltip") === ' +
            'undefined'
        ),
        true,
        'cloned tooltip data should be rebuilt for the clone'
    );
});

test('grouped: the cap comes from data-duplicate-max', function() {
    /* A body with only {x}, {x_1}, {x_2} advertises max 2, so three rows. */
    var window = load(variablesTable(loadBalancerPoolRows(), 2));

    clickAndAssertProgress(window, 'first click');
    clickAndAssertProgress(window, 'second click');

    assert.equal(rowCount(window), 6, 'two rows per group, three groups');
    assert.deepEqual(
        inputNames(window),
        ['cloud_server_id', 'load_balancer_pool_id',
         'cloud_server_id_1', 'load_balancer_pool_id_1',
         'cloud_server_id_2', 'load_balancer_pool_id_2'],
        'a third association row is available'
    );
    assert.equal(
        visiblePlusCount(window), 0,
        'no "+" remains once the body is full'
    );
    assert.equal(
        clickPlus(window), 'none',
        'the row cap never exceeds what the request body can hold'
    );
});

test('grouped: reaches ten rows when the body holds ten', function() {
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );

    for (var i = 1; i <= BULK_MAX_INDEX; i++) {
        clickAndAssertProgress(window, 'click ' + i);
    }

    assert.equal(rowCount(window), 20, 'ten association rows, two each');
    var names = inputNames(window);
    assert.equal(
        names[names.length - 2], 'cloud_server_id_9',
        'the last association row is _9, making ten in total'
    );
    assert.equal(
        visiblePlusCount(window), 0, 'the "+" is spent at the cap'
    );
    assert.equal(clickPlus(window), 'none', 'and cannot be clicked again');
});

test('grouped: the cap holds even if a hidden "+" is clicked', function() {
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );

    for (var i = 1; i <= BULK_MAX_INDEX; i++) {
        clickAndAssertProgress(window, 'click ' + i);
    }
    assert.equal(rowCount(window), 20, 'at the cap');

    assert.equal(
        forceClick(window, '.duplicate-field'), 'ok',
        'a stray click should not throw'
    );
    assert.equal(
        rowCount(window), 20,
        'no eleventh row: the request body has nowhere to put it'
    );
});

test('grouped: a hidden "-" cannot remove the base row', function() {
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );

    assert.equal(
        forceClick(window, '.remove-field'), 'ok',
        'a stray click should not throw'
    );
    assert.deepEqual(
        inputNames(window),
        ['cloud_server_id', 'load_balancer_pool_id'],
        'the base association row survives'
    );
});

test('grouped: clone inputs start empty', function() {
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );
    window.eval('jQuery("#cloud_server_id").val("server-a")');

    clickAndAssertProgress(window, 'first click');

    assert.equal(
        window.eval('jQuery("#cloud_server_id").val()'), 'server-a',
        'the original value survives cloning'
    );
    assert.equal(
        window.eval('jQuery("#cloud_server_id_1").val()'), '',
        'the clone is blank so it can hold a different association'
    );
});

test('grouped: no "-" until there is a row to remove', function() {
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );

    assert.equal(
        visibleRemoveCount(window), 0,
        'the base row cannot be removed, so no "-" is offered'
    );
    assert.equal(clickMinus(window), 'none', 'and none can be clicked');

    clickAndAssertProgress(window, 'first click');

    assert.equal(
        visibleRemoveCount(window), 1,
        'adding a row offers exactly one "-"'
    );
});

test('grouped: "-" drops the last association row', function() {
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );

    clickAndAssertProgress(window, 'first click');
    clickAndAssertProgress(window, 'second click');
    assert.equal(rowCount(window), 6, 'three association rows to start');

    assert.equal(clickMinus(window), 'ok', 'remove should not throw');

    assert.equal(rowCount(window), 4, 'the _2 rows are gone');
    assert.deepEqual(
        inputNames(window),
        ['cloud_server_id', 'load_balancer_pool_id',
         'cloud_server_id_1', 'load_balancer_pool_id_1'],
        'only the last row group is removed'
    );
    assert.equal(
        visiblePlusCount(window), 1,
        'the "+" comes back once there is room again'
    );
    assert.equal(
        visibleRemoveCount(window), 1,
        'the "-" moves to the new last row'
    );
});

test('grouped: "-" back to the base row hides itself', function() {
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );

    clickAndAssertProgress(window, 'first click');
    assert.equal(clickMinus(window), 'ok', 'remove should not throw');

    assert.equal(rowCount(window), 2, 'back to the base rows');
    assert.deepEqual(
        inputNames(window),
        ['cloud_server_id', 'load_balancer_pool_id'],
        'the base row is never removed'
    );
    assert.equal(visibleRemoveCount(window), 0, 'nothing left to remove');
    assert.equal(
        visiblePlusCount(window), 1, 'and the "+" is available again'
    );
});

test('grouped: add, remove and add again still names rows _1', function() {
    /* The cloned controls carry handlers from clone(true); a stale index here
     * would produce a gap such as _1, _3. */
    var window = load(
        variablesTable(loadBalancerPoolRows(), BULK_MAX_INDEX)
    );

    clickAndAssertProgress(window, 'first click');
    assert.equal(clickMinus(window), 'ok', 'remove should not throw');
    clickAndAssertProgress(window, 'click after removing');

    assert.deepEqual(
        inputNames(window),
        ['cloud_server_id', 'load_balancer_pool_id',
         'cloud_server_id_1', 'load_balancer_pool_id_1'],
        'the suffix sequence has no gap'
    );
    assert.equal(visibleRemoveCount(window), 1, 'one "-" is offered');
});

test('grouped: an unresolvable group leaves the "+" alone', function() {
    /* The button advertises a group no row belongs to. Nothing can be cloned,
     * so the click must be inert rather than consuming the '+'. */
    var window = load(variablesTable([
        {name: 'cloud_server_id', group: 'server_group_nodes', plus: true,
         buttonGroup: 'no_such_group'},
        {name: 'server_group_id', group: 'server_group_nodes'}
    ], BULK_MAX_INDEX));

    assert.equal(clickPlus(window), 'ok', 'click should not throw');
    assert.equal(rowCount(window), 2, 'no rows are added');
    assert.equal(
        visiblePlusCount(window), 1,
        'the "+" stays available instead of vanishing'
    );
});

test('legacy: ungrouped duplicates still clone one row at a time', function() {
    /* autoscale / servers network_uuid -- no duplicate_group. */
    var window = load(variablesTable([{name: 'network_uuid', plus: true}]));

    clickAndAssertProgress(window, 'first click');
    assert.deepEqual(
        inputNames(window), ['network_uuid', 'network_uuid_1'],
        'first clone is _1'
    );

    clickAndAssertProgress(window, 'second click');
    assert.deepEqual(
        inputNames(window),
        ['network_uuid', 'network_uuid_1', 'network_uuid_2'],
        'second clone is _2'
    );

    assert.equal(clickPlus(window), 'none', 'capped at three rows');
});

test('legacy: ungrouped duplicates honor a smaller cap', function() {
    var window = load(
        variablesTable([{name: 'network_uuid', plus: true}], 1)
    );

    clickAndAssertProgress(window, 'first click');
    assert.deepEqual(
        inputNames(window), ['network_uuid', 'network_uuid_1'],
        'only one clone is allowed'
    );
    assert.equal(clickPlus(window), 'none', 'stops at data-duplicate-max');
});

test('legacy: ungrouped duplicates honor a larger cap', function() {
    var window = load(
        variablesTable([{name: 'network_uuid', plus: true}], 5)
    );

    for (var i = 1; i <= 5; i++) {
        clickAndAssertProgress(window, 'click ' + i);
    }

    assert.deepEqual(
        inputNames(window),
        ['network_uuid', 'network_uuid_1', 'network_uuid_2',
         'network_uuid_3', 'network_uuid_4', 'network_uuid_5'],
        'larger bodies can expose every stored slot'
    );
    assert.equal(clickPlus(window), 'none', 'stops at the larger cap');
});

test('legacy: cloned tooltips do not share source tooltip data', function() {
    var window = load(
        variablesTable([{name: 'network_uuid', plus: true}], 2)
    );
    window.eval(
        'jQuery(".duplicate-field").first()' +
        '.data("bs.tooltip", {$element: "source"})'
    );

    clickAndAssertProgress(window, 'tooltip clone click');

    assert.equal(
        window.eval(
            'jQuery(".duplicate-field").last().data("bs.tooltip") === ' +
            'undefined'
        ),
        true,
        'cloned tooltip data should be rebuilt for the clone'
    );
});

test('legacy: ungrouped duplicates get no "-"', function() {
    /* Removing rows is scoped to grouped duplicates, so the autoscale and
     * servers network fields keep the behaviour they have always had. */
    var window = load(variablesTable([{name: 'network_uuid', plus: true}]));

    clickAndAssertProgress(window, 'first click');

    assert.equal(
        window.eval('jQuery(".remove-field").length'), 0,
        'no remove control is rendered for ungrouped duplicates'
    );
    assert.equal(clickMinus(window), 'none', 'nothing to click');
});


if (failures.length) {
    console.log('\n' + failures.length + ' failure(s):');
    failures.forEach(function(f) { console.log('  - ' + f); });
    process.exit(1);
}
console.log('\nall duplicate-row JS tests passed');
