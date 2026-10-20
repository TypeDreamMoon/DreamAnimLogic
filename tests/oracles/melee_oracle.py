"""Hand-written oracle for examples/melee_combo.dal.

It implements the *intended behaviour* of the three-hit chain directly as an explicit state machine,
without the dal package (it only talks to a host object through the documented host protocol:
``poll_events``, ``is_grounded``, ``montage_play``, ``set_max_walk_speed``).  The rules, in the order
one update applies them:

1. the attack buffer counts down;
2. events are handled one by one, oldest first: the cancel window follows the CancelWindow notify
   state (and closes when an attack montage ends), pressing Attack fills the buffer for 0.3 s, a Hit
   notify counts a hit, an attack montage that ends *on its own* ends the chain;
3. if the character left the ground the chain is dropped; otherwise standing + a buffered press +
   grounded starts hit 1, and during hit 1 / hit 2 a buffered press inside the cancel window starts the
   next hit (the third hit also scores a bonus hit and plays a little faster);
4. walking is slowed down while a chain runs and restored afterwards;
5. the commands go to the host after all of that.
"""

IDLE, HIT1, HIT2, HIT3 = 0, 1, 2, 3
MONTAGES = {HIT1: "Light1", HIT2: "Light2", HIT3: "Light3"}


class MeleeOracle:
    def __init__(self, host, walk_speed=600.0, attack_move_speed=120.0, light3_rate=1.15):
        self.host = host
        self.walk_speed = walk_speed
        self.attack_move_speed = attack_move_speed
        self.light3_rate = light3_rate
        self.step = IDLE
        self.buffer = 0.0
        self.cancel = False
        self.hits = 0
        self.was_attacking = False

    def update(self, dt):
        host = self.host
        grounded = bool(host.is_grounded)
        events = host.poll_events()
        commands = []

        if self.buffer > 0.0:
            self.buffer = max(self.buffer - dt, 0.0)

        for ev in events:
            kind, name = ev[0], ev[1]
            if kind == "notify_begin" and name == "CancelWindow":
                self.cancel = True
            elif kind == "notify_end" and name == "CancelWindow":
                self.cancel = False
            elif kind == "montage_ended" and name in MONTAGES.values():
                self.cancel = False
                interrupted = ev[2]
                if not interrupted and MONTAGES.get(self.step) == name:
                    self.step = IDLE
            elif kind == "input" and name == "Attack" and ev[2] == "started":
                self.buffer = 0.3
            elif kind == "notify" and name == "Hit":
                self.hits += 1

        if self.step != IDLE and not grounded:
            self.step = IDLE
        elif self.step == IDLE:
            if self.buffer > 0.0 and grounded:
                self.buffer = 0.0
                self.step = HIT1
                commands.append(("montage_play", ("Light1", 1.0, None)))
        elif self.step == HIT1:
            if self.buffer > 0.0 and self.cancel:
                self.buffer = 0.0
                self.step = HIT2
                commands.append(("montage_play", ("Light2", 1.0, None)))
        elif self.step == HIT2:
            if self.buffer > 0.0 and self.cancel:
                self.buffer = 0.0
                self.step = HIT3
                commands.append(("montage_play", ("Light3", self.light3_rate, None)))
                self.hits += 1

        attacking = self.step != IDLE
        if attacking != self.was_attacking:
            commands.append(("set_max_walk_speed", (self.attack_move_speed if attacking else self.walk_speed,)))
            self.was_attacking = attacking

        for name, args in commands:
            getattr(host, name)(*args)

    def snapshot(self):
        return {"Melee": self.step, "Hits": self.hits, "AttackBuffer": self.buffer, "Cancel": self.cancel, "WasAttacking": self.was_attacking}
