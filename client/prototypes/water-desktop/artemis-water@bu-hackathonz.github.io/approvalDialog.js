// The approval dialog: shows one Approval (contracts.md §2) and sends the user's decision.
// Reject is the default. A blocking approval is approved only with Shift+Enter on Approve.
// Escape always rejects. The rules live in approvalKeys.js; this file maps shell events to them.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';
import {MessageDialogContent} from 'resource:///org/gnome/shell/ui/dialog.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {initialFocus, keyDecision, clickDecision} from './approvalKeys.js';
import {describeApproval, accessibleName} from './approvalDescribe.js';

const DIFF_LINES = 30;
const KEY_NAMES = new Map([
    [Clutter.KEY_Return, 'Return'],
    [Clutter.KEY_KP_Enter, 'KP_Enter'],
    [Clutter.KEY_ISO_Enter, 'ISO_Enter'],
    [Clutter.KEY_space, 'space'],
    [Clutter.KEY_Escape, 'Escape'],
]);

function wrapped(text) {
    const label = new St.Label({text});
    label.clutter_text.line_wrap = true;
    return label;
}

export class ApprovalDialog {
    // onDecide(decision) returns a Promise of 'ok' | 'stale' | 'error'.
    constructor(approval, {onDecide}) {
        this._approval = approval;
        this._onDecide = onDecide;
        this._busy = false;
        this._desc = describeApproval(approval);
        // An approval we can't show starts on Reject, like a blocked one, and has no Approve button.
        const startBlocked = Boolean(approval.blocking) || !this._desc.known;
        this._focus = initialFocus(startBlocked);

        const title = this._desc.title;
        this._dialog = new ModalDialog.ModalDialog({
            styleClass: 'artemis-approval-dialog',
            destroyOnClose: true,
        });
        // Orca reads the title, the summary and the exact command or text when the dialog opens.
        this._dialog.accessible_name = accessibleName(approval);

        const content = new MessageDialogContent({
            title,
            description: this._desc.known ? approval.summary : this._desc.lines[0],
        });
        this._dialog.contentLayout.add_child(content);
        content.add_child(this._buildBody());

        this._status = wrapped('');
        content.add_child(this._status);

        this._reject = this._button('Reject', () => this._send(clickDecision('reject', approval)));
        this._reject.connect('key-focus-in', () => { this._focus = 'reject'; });
        if (this._desc.known) {
            this._approve = this._button(
                approval.blocking ? 'Approve blocked action (Shift+Enter)' : 'Approve',
                () => this._send(clickDecision('approve', approval)));
            this._approve.connect('key-focus-in', () => { this._focus = 'approve'; });
        }

        const row = new St.BoxLayout({style_class: 'actions', x_align: Clutter.ActorAlign.END});
        row.add_child(this._reject);
        if (this._approve)
            row.add_child(this._approve);
        content.add_child(row);

        this._dialog.connect('key-press-event', (_actor, event) => this._onKey(event));
        this._dialog.connect('key-release-event', (_actor, event) => this._onKeyRelease(event));
        this._dialog.setInitialKeyFocus(initialFocus(startBlocked) === 'reject' ? this._reject : this._approve);
    }

    get approvalId() {
        return this._approval.approval_id;
    }

    open() {
        this._openedAt = GLib.get_monotonic_time(); // µs; the reveal guard counts from here
        return this._dialog.open(global.get_current_time());
    }

    close() {
        if (this._closed) return; // the dialog closes itself after a decision; a later close is a no-op
        this._closed = true;
        this._dialog.close(global.get_current_time());
    }

    _buildBody() {
        const a = this._approval;
        const body = new St.BoxLayout({vertical: true, x_expand: true});
        if (!this._desc.known) {
            body.add_child(wrapped('Press Enter or Escape to reject.'));
            return body;
        }
        for (const line of this._desc.lines)
            body.add_child(wrapped(line));
        if (a.findings?.length > 0) {
            body.add_child(wrapped('Security findings'));
            for (const f of a.findings)
                body.add_child(wrapped(`${f.severity}: ${f.rule} in ${f.path}, line ${f.line}: ${f.message}`));
        }
        if (a.diff) {
            const lines = a.diff.split('\n');
            const shown = lines.slice(0, DIFF_LINES).join('\n');
            const more = lines.length > DIFF_LINES ? `\n... ${lines.length - DIFF_LINES} more lines not shown.` : '';
            const diff = new St.Label({text: shown + more});
            diff.clutter_text.line_wrap = true;
            diff.accessible_name = 'Proposed diff';
            const scroll = new St.ScrollView({
                hscrollbar_policy: St.PolicyType.AUTOMATIC,
                vscrollbar_policy: St.PolicyType.AUTOMATIC,
                style: 'max-height: 16em;',
            });
            // St.ScrollView needs a scrollable child: a BoxLayout, not the label itself.
            const box = new St.BoxLayout({vertical: true});
            box.add_child(diff);
            scroll.set_child(box);
            body.add_child(scroll);
        }
        body.add_child(wrapped(a.blocking
            ? 'Blocked action. Review the security findings. To override, focus Approve and press Shift+Enter. Enter alone cannot approve. Escape rejects.'
            : 'Choose a button and press Enter. Escape rejects this proposal.'));
        return body;
    }

    _button(label, onClick) {
        const button = new St.Button({label, style_class: 'button', can_focus: true});
        button.connect('clicked', onClick);
        // The dialog decides keys itself, so St.Button never clicks from the keyboard.
        button.connect('key-press-event', (_actor, event) => this._onKey(event));
        button.connect('key-release-event', (_actor, event) => this._onKeyRelease(event));
        return button;
    }

    _keyFromEvent(event) {
        const state = event.get_state();
        return {
            name: KEY_NAMES.get(event.get_key_symbol()) ?? 'other',
            shift: Boolean(state & Clutter.ModifierType.SHIFT_MASK),
            ctrl: Boolean(state & Clutter.ModifierType.CONTROL_MASK),
            alt: Boolean(state & Clutter.ModifierType.MOD1_MASK),
            super: Boolean(state & Clutter.ModifierType.SUPER_MASK),
            repeated: Boolean(event.get_flags() & Clutter.EventFlags.FLAG_REPEATED),
        };
    }

    _onKey(event) {
        const key = this._keyFromEvent(event);
        if (key.name === 'other')
            return Clutter.EVENT_PROPAGATE;
        if (this._busy)
            return Clutter.EVENT_STOP;
        const sinceOpenMs = this._openedAt ? (GLib.get_monotonic_time() - this._openedAt) / 1000 : 0;
        const decision = keyDecision(key, this._focus, this._approval, sinceOpenMs);
        if (decision)
            this._send(decision);
        return Clutter.EVENT_STOP;
    }

    _onKeyRelease(event) {
        return KEY_NAMES.has(event.get_key_symbol()) ? Clutter.EVENT_STOP : Clutter.EVENT_PROPAGATE;
    }

    _setSensitive(on) {
        this._reject.reactive = on;
        if (this._approve)
            this._approve.reactive = on;
    }

    async _send(decision) {
        if (this._busy || !decision)
            return; // a blocked Approve click decides nothing
        this._busy = true;
        this._setSensitive(false);
        this._status.text = 'Sending decision…';

        let result;
        try {
            result = await this._onDecide(decision);
        } catch (_e) {
            result = 'error';
        }

        if (result === 'ok') {
            this.close();
        } else if (result === 'stale') {
            Main.notify('Artemis', 'This approval is out of date, so nothing was changed.');
            this.close();
        } else {
            this._busy = false;
            this._setSensitive(true);
            this._status.text = 'Decision failed. Please try again.';
        }
    }
}
