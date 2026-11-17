"""Estado: régimen local. Ley 7/1985."""

from realms.legislation import Ref, above, extension


class LocalRegime(above("local_regime")):
    uses = ["procedure"]

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def check_proposal_create(self, args):
        record = type(args)     # an action, or its args, that is no record is for the realm to refuse: this guard cannot judge it
        action = args.action
        if not isinstance(action, record) or action.op != "codex_install" \
                or not isinstance(action.args, record) or "checksum" not in action.args:   # only this realm's own change of legislation
            return None
        if args.effective_at is not None and not isinstance(args.effective_at, int):    # no time: the realm refuses it
            return None
        earliest_close_day = self.local_day(self.now())      # a vote may close as soon as its outcome is certain
        if args.effective_at is None \
                or self.local_day(args.effective_at) < self.legislation.procedure.working_days_after(earliest_close_day, 15):
            return self.deny("an ordinance takes effect at the earliest 15 working days after the vote closes (Art. 65.2)")

    @extension(legislation="Ley 7/1985 Art. 65.2")
    def on_codex_install(self, event):
        codex = event.result.get("codex")     # an install that only starts its job has no codex to name yet
        if codex is None:
            return
        for authority in ("AGE", "GVA"):
            self.ggg.notification_send(recipient=Ref("external", authority), topic="ordinance",
                                       title="Comunicació d'acord", body=codex)
