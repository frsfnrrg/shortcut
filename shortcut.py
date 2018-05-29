#!/usr/bin/env python3

"""
What is this? a shortcut tester.

Requires:

xev 
Python 3.6+

Run like ./shortcut.py
"""

from collections import namedtuple
from enum import Enum
import asyncio
import sys

class Action(Enum):
    PRESS = 0
    RELEASE = 1

KeyEvent = namedtuple("KeyEvent", "action state keycode keysym keysymname")
command = ['xev', '-event', 'keyboard']
tmap = {b'KeyPress':Action.PRESS, b'KeyRelease':Action.RELEASE}
avoid = (b'XLookupString', b'XFilterEvent', b'XmbLookupString', b'XFilterEvent'
    , b'XKeysymToKeycode', b'"')
vispad = lambda x: x + ' ' * (20 - len(x))

async def read_xev_stream(stream, queue):
    state = 0
    tcode = None
    while True:
        line = await stream.readline()
        if not line:
            break
        line = line.strip()

        if state == 0 and line.split(b' ', 1)[0] in tmap:
            tcode = tmap[line.split(b' ', 1)[0]]
            state = 1
        elif state == 1 and line.split(b' ', 1)[0] == b'root':
            state = 2
        elif state == 2 and line.split(b' ', 1)[0] == b'state':
            l = line.index(b'), same_screen')
            line = line[:l]
            sk, sy = line.split(b'(', 1)
            state, keycode = sk.split(b',', 1)
            keysym, keysymname = sy.split(b',', 1)

            state = int(state[state.index(b'0x') + 2:], 16)
            keycode = int(keycode.strip().split(b' ')[1])
            keysym = int(keysym.strip().split(b' ')[1][2:], 16)

            keysymname = keysymname.strip().decode()

            key_event = KeyEvent(action=tcode, state=state, keycode=keycode,
                keysym=keysym, keysymname=keysymname)

            await queue.put(key_event)

            state = 0
        else:
            state = 0

    await queue.put(None)

async def process_stream(cmd, queue):
    process = await asyncio.create_subprocess_exec(*cmd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

    await read_xev_stream(process.stdout, queue)

    return await process.wait()

async def broadcast(iqueue, oqueues):
    while True:
        item = await iqueue.get()

        for o in oqueues:
            await o.put(item)

        if item is None:
            break

async def shortcut_kde(queue):
    """
    What KDE does
    """
    reserved = {'Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Super_L',
        'Super_R', 'Shift_L', 'Shift_R', 'ISO_Level3_Shift', 'Hyper_L',
        'Hyper_R', 'Meta_L', 'Meta_R'}
    mods = [('Meta', 0x40), ('Ctrl', 0x4), ('Alt', 0x8), ('Shift', 0x1)]

    while True:
        key = await queue.get()
        if key is None:
            break

        if key.keysymname in reserved:
            continue
        if key.action == Action.RELEASE:
            continue

        comboname = []
        for mod, flag in mods:
            if key.state & flag:
                comboname.append(mod)
        comboname.append(key.keysymname)

        print(vispad('KDE'), '+'.join(comboname))

async def shortcut_i3(queue):
    """
    What i3 does.
    https://i3wm.org/docs/userguide.html
    """
    mods = [('Shift', 0x01), ('Ctrl', 0x04), ('Mod1', 0x08), ('Mod2', 0x10),
        ('Mod3', 0x20), ('Mod4', 0x40), ('Mod5', 0x80)]

    while True:
        key = await queue.get()
        if key is None:
            break

        prefix = '--release ' if key.action == Action.RELEASE else ''

        active_mods = []
        for mod, flag in mods:
            if key.state & flag:
                active_mods.append(mod)
        # Note: i3 handles Group modifiers, which we ignore here
        symform = active_mods + [key.keysymname]
        codeform = active_mods + [str(key.keycode)]
        print(vispad('i3_bindsym'), prefix + '+'.join(symform))
        print(vispad('i3_bindcode'), prefix + '+'.join(codeform))

async def shortcut_sway(queue):
    """
    Bindings are a modifier mask, release switch, 
      and keysym set (for bindsym)
      and keycode set (for bindcode)
    """
    modmasknames = [('Shift', 0x01), ('Caps', 0x02), ('Ctrl', 0x04),
        ('Mod1', 0x08), ('Mod2', 0x10), ('Mod3', 0x20), ('Mod4', 0x40),
        ('Mod5', 0x80)]
    # 'Alt'=Mod1

    modifiers = {'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Caps_Lock',
        'Shift_Lock', 'Meta_L', 'Meta_R', 'Alt_L', 'Alt_R', 'Super_L',
        'Super_R', 'Hyper_L', 'Hyper_R'}

    # Which keycodes are modifiers: via (xkb_state_key_get_syms), syms for code;
    # then modifier
    modifier_keycodes = set()

    # Keycode names are available at linux/input-event-codes.h

    reserved = {}

    pressed_keysyms = set()
    pressed_keycodes = set()

    while True:
        key = await queue.get()
        if key is None:
            break

        # Divine which keycodes map to modifier keysyms
        # (implicit xkb_state_key_get_syms)
        if key.keysymname in modifiers:
            modifier_keycodes.add(key.keycode)

        if key.action == Action.RELEASE:
            active_mods = [m for m, k in modmasknames if not key.state ^ k]
            symcombo = active_mods + sorted(pressed_keysyms)
            prefix = '--release '

            active_codes = pressed_keycodes - modifier_keycodes
            if key.keycode in pressed_keycodes:
                codecombo = active_mods + [str(s) for s in sorted(active_codes)
                    ]
            else:
                codecombo = None

            if key.keysymname not in modifiers:
                try:
                    pressed_keysyms.remove(key.keysymname)
                except KeyError:
                    # Window manager may eat shortcut key press but not release
                    pass
            try:
                pressed_keycodes.remove(key.keycode)
            except KeyError:
                # Might have been eaten
                pass
        else:
            if key.keysymname not in modifiers:
                # Limited to 32 keys simultanously in total, but it's never happen
                pressed_keysyms.add(key.keysymname)
            pressed_keycodes.add(key.keycode)

            active_mods = [m for m, k in modmasknames if not key.state ^ k]
            symcombo = active_mods + sorted(pressed_keysyms)
            active_codes = pressed_keycodes - modifier_keycodes
            codecombo = active_mods + [str(s) for s in sorted(active_codes)]
            prefix = ''

        # NOTE: xev only gives us the translated keysyms, while
        # xkb_keymap_key_get_syms, xkb_state_key_get_consumed_mods2
        # are needed for raw keycode=>keysym translation
        # (note that it isn't xkb_keymap_key_get_syms_by_level)
        print(vispad('sway_bindsym'), prefix + '+'.join(symcombo))
        if codecombo is not None:
            print(vispad('sway_bindcode'), prefix + '+'.join(codecombo))

async def shortcut_delta(queue):
    """
    A proposed keysym handling method
    """

    MAX_SHORTCUT_LENGTH = 3

    def format_key(key):
        # Use i3 approach
        mods = [('Shift', 0x01), ('Ctrl', 0x04), ('Mod1', 0x08), ('Mod2', 0x10)
            , ('Mod3', 0x20), ('Mod4', 0x40), ('Mod5', 0x80)]
        active_mods = []
        for mod, flag in mods:
            if key.state & flag:
                active_mods.append(mod)
        name = '+'.join(active_mods + [key.keysymname])
        return name

    lkey = None
    last_modifier_state = 0
    prev_keys = []

    while True:
        key = await queue.get()
        if key is None:
            break

        # On press, drop the previous stored key if it affected modifiers
        if (last_modifier_state != key.state and len(prev_keys) and key.action
            == Action.PRESS and prev_keys[-1] == lkey):
            prev_keys = prev_keys[:-1]

        if key.action == Action.PRESS:
            netname = [format_key(k) for k in prev_keys] + [format_key(key)]
        else:
            netname = [format_key(k) for k in prev_keys]

        if key.action == Action.PRESS:
            prev_keys.append(key)
            if len(prev_keys) > MAX_SHORTCUT_LENGTH:
                prev_keys = prev_keys[1:]
        # Update memfree vars
        lkey = key
        last_modifier_state = key.state

        """
        TODO: include Unicode case normalization and bindcode variation handling
        """
        for i in range(1, MAX_SHORTCUT_LENGTH + 1):
            comboname = '/'.join(netname[::-1][:i][::-1])
            if key.action == Action.RELEASE:
                comboname = '--release ' + comboname
            print(vispad('Δ_bindsym'), comboname)
            if i == 1 and comboname == 'Ctrl+r':
                prev_keys = []

def main(workers):
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue(loop=loop)

    oqueues = [asyncio.Queue(loop=loop) for w in workers]

    consumer_coros = [worker(queue) for worker, queue in zip(workers, oqueues)]

    producer_coro = process_stream(command, queue)
    mux_coro = broadcast(queue, oqueues)

    loop.run_until_complete(
        asyncio.gather(producer_coro, mux_coro, *consumer_coros))
    loop.close()

if __name__ == '__main__':
    # Restrict this list if there's too much output
    workers = [shortcut_kde, shortcut_i3, shortcut_sway, shortcut_delta]
    main(workers)
