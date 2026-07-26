"""CTC controller models and the subsystems each one typically has.

Why this exists: a controller answers *every* system register whether or not
the hardware behind it is installed. Verified on an EcoLogic M - `verify
--system` reports "87 present, 0 not implemented", with no solar fitted yet
sunTempOut/sunTempIn reading 1000 and sunPump 100%, and no ventilation unit yet
sFanExhaustPct reading 100% and sVentMaintFilterDays 83. So neither silence nor
plausible-looking data tells you what is really there, and the model is the
better starting point.

These are *defaults*, not a hard filter. A model permits more than any one
install has (an EcoLogic M can drive a pool or solar; most don't), so the model
only decides which subsystem boxes start ticked and the options flow is where
the user corrects it. Never turn this into a hard exclusion - a wrong row would
hide entities with no way back.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .groups import SUBSYSTEMS


@dataclass(frozen=True)
class Model:
    name: str
    # sProductType (62253) values that identify this model, where known.
    product_types: tuple[int, ...] = ()
    # Subsystems ticked by default.
    subsystems: tuple[str, ...] = field(default_factory=tuple)


# Model names are the ones the BMS manual itself uses in its register
# descriptions ("EcoHeat 400 / EcoZenith i250", "GSi/EcoZenith i350",
# "EcoLogic / EcoZenith i550").
#
# Evidence quality varies, and the comments say which is which:
#   ecologic_m - grounded: read off a live unit, sProductType 14, owner
#                confirmed DHW + additional heat and nothing else.
#   everything else - best-effort from the manual's model mentions. Correct a
#                row when someone reports real hardware; don't guess wider.
MODELS: dict[str, Model] = {
    "ecologic_m": Model(
        "CTC EcoLogic M",
        product_types=(14,),
        subsystems=("DHW", "AddHeat"),
    ),
    "ecologic_l": Model(
        "CTC EcoLogic L",
        subsystems=("DHW", "AddHeat"),
    ),
    "ecozenith_i250": Model(
        "CTC EcoZenith i250",
        subsystems=("DHW", "AddHeat"),
    ),
    "ecozenith_i350": Model(
        "CTC EcoZenith i350",
        subsystems=("DHW", "AddHeat", "Ventilation"),
    ),
    "ecozenith_i550": Model(
        "CTC EcoZenith i550",
        subsystems=("DHW", "AddHeat", "Ventilation"),
    ),
    "ecoheat_400": Model(
        "CTC EcoHeat 400",
        subsystems=("DHW", "AddHeat"),
    ),
    "gsi": Model(
        "CTC GSi",
        subsystems=("DHW", "AddHeat"),
    ),
    # The escape hatch: no assumptions, every subsystem ticked. Also what an
    # unrecognised sProductType falls back to.
    "other": Model(
        "Other / not listed",
        subsystems=tuple(SUBSYSTEMS),
    ),
}

DEFAULT_MODEL = "other"


def model_for_product_type(code: int | None) -> str:
    """Model key for an sProductType reading, or DEFAULT_MODEL if unknown."""
    if code is not None:
        for key, model in MODELS.items():
            if code in model.product_types:
                return key
    return DEFAULT_MODEL
