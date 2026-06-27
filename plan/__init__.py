"""Hyrox-only training plan.

Generates a periodized weekly Hyrox strength-and-conditioning plan toward a
target race, auto-regulated by recent Garmin training-load and recovery data.
Running is NOT prescribed here — the athlete follows her own separate running
plan; this generator reads her upcoming scheduled runs from Garmin so it can
place hard station/strength work away from her hard run days.
"""
