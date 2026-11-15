// Preferences window: the water's look and feel, changed live while you move the mouse.
import Adw from 'gi://Adw';
import Gtk from 'gi://Gtk';
import {VOICES} from './voices.js';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const ROWS = [
    ['orb-size', 'Agent orb size', 'Diameter of the agent\'s glass orb (px)', 32, 200, 2, 0],
    ['lens-size', 'Lens size', 'Diameter of the lens (px)', 24, 160, 2, 0],
    ['lens-zoom', 'Lens zoom', 'Magnification at the centre of the lens', 1, 2.5, 0.05, 2],
    ['wake-width', 'Wake width', 'Width of the wake and ripple fronts (px)', 6, 80, 1, 0],
    ['distortion', 'Distortion strength', 'How much the wallpaper bends under the water', 0, 3, 0.05, 2],
    ['ripple-life', 'Ripple lifetime', 'Seconds before ripples fade out (lower = more damping)', 0.4, 3, 0.05, 2],
    ['active-radius', 'Reach', 'How far the water spreads around the trail (px)', 30, 600, 2, 0],
    ['highlights', 'Highlights', 'Brightness of the thin glossy lines', 0, 1.5, 0.05, 2],
];

export default class ArtemisWaterPrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const page = new Adw.PreferencesPage({title: 'Water', icon_name: 'weather-showers-symbolic'});
        const group = new Adw.PreferencesGroup({
            title: 'Look and feel',
            description: 'Changes apply immediately. Run a command from the sphere (or the top-bar button) to try them.',
        });
        const driver = new Adw.ComboRow({
            title: 'Driven by',
            subtitle: 'Agent: a glass sphere in the bottom-left corner runs commands. Pointer: the effect follows your mouse.',
            model: Gtk.StringList.new(['Agent sphere', 'Mouse pointer']),
        });
        const drivers = ['agent', 'pointer'];
        driver.selected = Math.max(drivers.indexOf(settings.get_string('driver')), 0);
        driver.connect('notify::selected', () => settings.set_string('driver', drivers[driver.selected]));
        group.add(driver);
        const agentLook = new Adw.ComboRow({
            title: 'Agent look',
            subtitle: 'Island: a glowing pill under the top bar. Ring: liquid glass in a circle. Orb: a morphing glass orb.',
            model: Gtk.StringList.new(['Top-edge island', 'Liquid ring', 'Glass orb']),
        });
        const looks = ['island', 'ring', 'orb'];
        agentLook.selected = Math.max(looks.indexOf(settings.get_string('agent-look')), 0);
        agentLook.connect('notify::selected', () => settings.set_string('agent-look', looks[agentLook.selected]));
        group.add(agentLook);
        const fonts = ['Inter', 'Space Grotesk', 'Manrope', 'Outfit', 'Sora', 'Ubuntu Sans', 'JetBrains Mono'];
        const font = new Adw.ComboRow({title: 'Font', subtitle: 'For the island\'s panel and board', model: Gtk.StringList.new(fonts)});
        font.selected = Math.max(fonts.indexOf(settings.get_string('ui-font')), 0);
        font.connect('notify::selected', () => settings.set_string('ui-font', fonts[font.selected]));
        group.add(font);
        const voice = new Adw.ComboRow({title: 'Voice', subtitle: 'How Artemis speaks (free ElevenLabs voices; a preview plays when you switch)',
            model: Gtk.StringList.new(VOICES.map((v) => `${v.name} \u2014 ${v.about}`))});
        voice.selected = Math.max(VOICES.findIndex((v) => v.key === settings.get_string('voice')), 0);
        voice.connect('notify::selected', () => settings.set_string('voice', VOICES[voice.selected].key));
        group.add(voice);
        const wake = new Adw.SwitchRow({title: 'Listen for \u201cHey Artemis\u201d', subtitle: 'Hands-free: say its name, then your request. Heard on this computer only; other speech is ignored.'});
        settings.bind('wake-word', wake, 'active', 0);
        group.add(wake);
        const motion = new Adw.SwitchRow({
            title: 'Animate the orb while it rests',
            subtitle: 'It slowly morphs and tumbles. Off: it holds still until a command runs (lighter on the GPU).',
        });
        settings.bind('orb-motion', motion, 'active', 0);
        group.add(motion);
        const style = new Adw.ComboRow({
            title: 'Style',
            subtitle: 'Pointer mode only. Lens: a small glass lens follows the pointer. Water: ripples follow it.',
            model: Gtk.StringList.new(['Lens', 'Water']),
        });
        const styles = ['lens', 'water'];
        style.selected = Math.max(styles.indexOf(settings.get_string('style')), 0);
        style.connect('notify::selected', () => settings.set_string('style', styles[style.selected]));
        group.add(style);
        const scope = new Adw.SwitchRow({
            title: 'Bend everything on screen',
            subtitle: 'Windows, their contents, the top bar and the dock bend too. Off: wallpaper only.',
        });
        settings.bind('cover-everything', scope, 'active', 0);
        group.add(scope);
        for (const [key, title, subtitle, min, max, step, digits] of ROWS) {
            const row = new Adw.SpinRow({
                title,
                subtitle,
                digits,
                adjustment: new Gtk.Adjustment({lower: min, upper: max, step_increment: step, page_increment: step * 10}),
            });
            settings.bind(key, row, 'value', 0);
            group.add(row);
        }
        page.add(group);

        const reset = new Adw.PreferencesGroup();
        const button = new Gtk.Button({label: 'Reset to defaults', halign: Gtk.Align.START});
        button.connect('clicked', () => { ROWS.forEach(([key]) => settings.reset(key)); settings.reset('cover-everything'); settings.reset('style'); settings.reset('driver'); settings.reset('orb-motion'); settings.reset('orb-home'); settings.reset('agent-look'); });
        reset.add(button);
        page.add(reset);
        window.add(page);
    }
}
