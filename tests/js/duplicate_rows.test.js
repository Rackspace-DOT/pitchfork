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
    'function update_duplicate_field',
    'function add_duplicate_group',
    '.duplicate-field'
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
 * group renders the '+'. */
function variablesTable(rows) {
    var html = rows.map(function(row) {
        var isGroup = !!row.group;
        var plus = row.plus
            ? '<a class="duplicate-field tooltip-title" title="Add Row"' +
              ' data-duplicate-group="' + (row.group || '') + '"></a>'
            : '';
        return '<tr' + (isGroup
                ? ' class="duplicate-group-row" data-duplicate-group="' +
                  row.group + '"'
                : '') + '>' +
            '<td>' + plus + '</td>' +
            '<td class="variable-name-cell">' + row.name + '</td>' +
            '<td><input id="' + row.name + '" name="' + row.name +
            '" type="text"></td>' +
            '</tr>';
    }).join('');
    return '<table id="t"><tbody>' + html + '</tbody></table>';
}

/* "Add Nodes to Load Balancer Pools" -- three variables, one group. */
function loadBalancerPoolRows() {
    return [
        {name: 'cloud_server_id', group: 'load_balancer_pool_nodes',
         plus: true},
        {name: 'port', group: 'load_balancer_pool_nodes'},
        {name: 'load_balancer_pool_id', group: 'load_balancer_pool_nodes'}
    ];
}


function inputNames(window) {
    return window.eval(
        'jQuery("#t input").map(function(){' +
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

/* Click the first still-visible '+'. Returns 'ok', 'none' when there is
 * nothing left to click, or 'threw: ...'. A throw is the PR #68 failure. */
function clickPlus(window) {
    return window.eval(
        '(function(){' +
        '  var b = jQuery(".duplicate-field").filter(function(){' +
        '    return this.style.display !== "none"; }).first();' +
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
    var window = load(variablesTable(loadBalancerPoolRows()));

    clickAndAssertProgress(window, 'first click');

    assert.equal(rowCount(window), 6, 'three base rows plus three clones');
    assert.deepEqual(
        inputNames(window),
        ['cloud_server_id', 'port', 'load_balancer_pool_id',
         'cloud_server_id_1', 'port_1', 'load_balancer_pool_id_1'],
        'clone inputs are suffixed _1'
    );
    assert.deepEqual(
        rowLabels(window),
        ['cloud_server_id', 'port', 'load_balancer_pool_id',
         'cloud_server_id_1', 'port_1', 'load_balancer_pool_id_1'],
        'displayed parameter names track the input names'
    );
    assert.equal(
        visiblePlusCount(window), 1,
        'the source "+" is spent and the clone offers the next one'
    );
});

test('grouped: a second click clones to _2 and then stops', function() {
    var window = load(variablesTable(loadBalancerPoolRows()));

    clickAndAssertProgress(window, 'first click');
    clickAndAssertProgress(window, 'second click');

    assert.equal(rowCount(window), 9, 'three rows per group, three groups');
    assert.deepEqual(
        inputNames(window),
        ['cloud_server_id', 'port', 'load_balancer_pool_id',
         'cloud_server_id_1', 'port_1', 'load_balancer_pool_id_1',
         'cloud_server_id_2', 'port_2', 'load_balancer_pool_id_2'],
        'a third association row is available'
    );
    assert.equal(
        visiblePlusCount(window), 0,
        'no "+" remains once the body is full'
    );
    assert.equal(
        clickPlus(window), 'none',
        'the row cap matches the three-element request body'
    );
});

test('grouped: clone inputs start empty', function() {
    var window = load(variablesTable(loadBalancerPoolRows()));
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

test('grouped: an unresolvable group leaves the "+" alone', function() {
    /* The button advertises a group no row belongs to. Nothing can be cloned,
     * so the click must be inert rather than consuming the '+'. */
    var window = load(variablesTable([
        {name: 'cloud_server_id', group: 'server_group_nodes', plus: true},
        {name: 'server_group_id', group: 'server_group_nodes'}
    ]).replace(
        'data-duplicate-group="server_group_nodes"></a>',
        'data-duplicate-group="no_such_group"></a>'
    ));

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


if (failures.length) {
    console.log('\n' + failures.length + ' failure(s):');
    failures.forEach(function(f) { console.log('  - ' + f); });
    process.exit(1);
}
console.log('\nall duplicate-row JS tests passed');
