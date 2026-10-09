"""Typed, undoable USD edits in the selected layer; imported only by the worker."""
import json
import math
import os
from pathlib import Path
import tempfile

from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux
from .usd_schemas import apply_schema

PRIM_TYPES = ("Xform", "Scope", "Sphere", "Cube", "Cone", "Cylinder", "Capsule", "Plane",
              "Camera", "DistantLight", "DomeLight", "RectLight", "DiskLight", "CylinderLight", "SphereLight", "Material")
MAX_VALUES = 100000


def editable_prim(prim, children=False):
    from .usd_preview_camera import is_locked
    return bool(prim and prim.IsActive() and not prim.IsInstanceProxy() and not prim.IsInPrototype()
                and (not children or not prim.IsInstance()) and not is_locked(prim))


def activation_editable(prim):
    from .usd_preview_camera import is_locked
    return bool(prim and not prim.IsPseudoRoot() and not prim.IsInstanceProxy()
                and not prim.IsInPrototype() and not is_locked(prim))


def supported_type(type_name):
    default = type_name.scalarType.defaultValue
    return isinstance(default, (bool, int, float, str, Sdf.AssetPath)) or type(default).__module__ == "pxr.Gf" and (
        hasattr(default, "dimension") or hasattr(default, "GetReal"))


def encode_value(value, budget=None):
    if budget is None:
        budget = [MAX_VALUES]
    budget[0] -= 1
    if budget[0] < 0:
        raise ValueError("This value is too large for the property editor (100,000 components maximum).")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Sdf.AssetPath):
        return value.path
    if hasattr(value, "GetReal"):
        return [value.GetReal(), *value.GetImaginary()]
    return [encode_value(part, budget) for part in value]


def decode_value(type_name, value):
    if type_name.isArray:
        if not isinstance(value, list) or len(value) > MAX_VALUES:
            raise ValueError("Expected a JSON array with at most 100,000 elements.")
        return type_name.type.pythonClass([decode_value(type_name.scalarType, part) for part in value])
    default = type_name.defaultValue
    if isinstance(default, bool):
        if type(value) is not bool:
            raise ValueError("Expected true or false.")
    elif isinstance(default, int):
        if type(value) is not int:
            raise ValueError("Expected a whole number.")
    elif isinstance(default, float):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Expected a finite number.")
        value = float(value)
    elif isinstance(default, (str, Sdf.AssetPath)):
        if not isinstance(value, str):
            raise ValueError("Expected text.")
        if isinstance(default, Sdf.AssetPath):
            return Sdf.AssetPath(value)
    elif type(default).__module__ == "pxr.Gf":
        def finite(parts):
            if not isinstance(parts, list):
                return type(parts) in (int, float) and math.isfinite(parts)
            return all(finite(p) for p in parts)
        if not isinstance(value, list) or not finite(value):
            raise ValueError("Expected a JSON array of finite numbers.")
        cls = type(default)
        if hasattr(default, "GetReal"):
            if len(value) != 4:
                raise ValueError("Quaternions require [real, x, y, z].")
            return cls(value[0], type(default.GetImaginary())(*value[1:]))
        return cls(*value)
    else:
        raise ValueError("This USD value type is not editable.")
    return value


def property_info(prim, group, name, time, *, use_default=False):
    from .stage_metadata import FIELDS
    if prim and prim.IsPseudoRoot() and group == "Metadata" and name in FIELDS:
        from .usd_stage_metadata import stage_metadata
        value = stage_metadata(prim.GetStage())[name]
        return dict(path="/", group=group, name=name, frame=time.GetValue(), value=value,
                    type="token" if name == "upAxis" else "string" if isinstance(value, str) else "double",
                    options=["Y", "Z"] if name == "upAxis" else [])
    from .usd_transform_lock import is_transform_property, require_unlocked
    if group == "Attributes" and is_transform_property(name):
        require_unlocked(prim)
    if not editable_prim(prim) or prim.IsPseudoRoot():
        raise ValueError("Select an editable prim. Instance contents must be edited at their source.")
    result = dict(path=str(prim.GetPath()), group=group, name=name, frame=time.GetValue())
    if group == "Attributes":
        attr = prim.GetAttribute(name)
        if not attr or not supported_type(attr.GetTypeName()):
            raise ValueError("This attribute type is not editable.")
        value = attr.Get(Usd.TimeCode.Default() if use_default else time)
        if value is None:
            value = attr.GetTypeName().defaultValue
        result.update(type=str(attr.GetTypeName()), value=encode_value(value),
                      animated=attr.ValueMightBeTimeVarying(), uniform=attr.GetVariability() == Sdf.VariabilityUniform,
                      options=list(attr.GetMetadata("allowedTokens") or []), connected=attr.HasAuthoredConnections())
        samples = attr.GetTimeSamples()
        result.update(sample_times=samples[:1000], sample_count=len(samples),
                      has_sample=not use_default and time.GetValue() in samples)
    elif group == "Relationships":
        rel = prim.GetRelationship(name)
        if not rel:
            raise ValueError("This relationship no longer exists.")
        result.update(type="relationship", value=[str(path) for path in rel.GetTargets()])
    elif group == "Metadata" and name in ("documentation", "displayName", "hidden", "kind"):
        value = prim.GetMetadata(name)
        result.update(type="bool" if isinstance(value, bool) else "string", value=value)
    else:
        raise ValueError("This composition metadata is read-only.")
    if len(json.dumps(result)) > 2_000_000:
        raise ValueError("This value is too large for the property editor.")
    return result


class StageEdits:
    def __init__(self, stage):
        self.stage = stage
        stage.SetEditTarget(stage.GetRootLayer())
        self.undo = []
        self.redo = []
        self.saved = {}
        self.initial = {}
        self.layer_cache = {}
        self.track_layer(self.layer)
        from .usd_layers import retain_layers
        retain_layers(stage, self)

    @property
    def layer(self):
        return self.stage.GetEditTarget().GetLayer()

    def track_layer(self, layer):
        # Stage/edit-target accessors return weak layer handles. Acquire an
        # owning handle so dirty checks and undo remain valid after detachment.
        retained = Sdf.Layer.FindOrOpen(layer.identifier)
        if retained not in self.saved:
            self.saved[retained] = retained.ExportToString()
        # Capture before the first edit. Saving advances the dirty baseline,
        # but must not advance the starting state used by layer revert.
        self.initial.setdefault(retained, self.saved[retained])
        return retained

    def set_edit_target(self, identifier):
        layer = next((layer for layer in self.stage.GetLayerStack() if layer.identifier == identifier), None)
        if layer is None:
            raise ValueError("Choose a layer in the local stage stack. Referenced layers can be inspected only.")
        if not layer.permissionToEdit:
            raise ValueError("This layer does not permit editing.")
        layer = self.track_layer(layer)
        # Preserve any sublayer time offset/scale when authoring stage-time samples.
        self.stage.SetEditTarget(self.stage.GetEditTargetForLocalLayer(layer))

    def modified(self, layer):
        return layer in self.saved and layer.ExportToString() != self.saved[layer]

    def state(self):
        return dict(dirty=any(self.modified(layer) for layer in self.saved),
                    undo=self.undo[-1][0] if self.undo else "", redo=self.redo[-1][0] if self.redo else "")

    def ensure_edit_target(self):
        layer = self.layer
        if layer not in self.stage.GetLayerStack():
            layer = self.stage.GetRootLayer()
        self.stage.SetEditTarget(self.stage.GetEditTargetForLocalLayer(layer))

    def change(self, label, action, layer=None, *, record=True):
        layer = self.track_layer(layer if layer is not None else self.layer)
        if not layer.permissionToEdit:
            raise ValueError("The edit target does not permit editing.")
        before = layer.ExportToString()
        try:
            path = action()
        except Exception:
            layer.ImportFromString(before)
            self.ensure_edit_target()
            raise
        self.ensure_edit_target()
        if record:
            self.record_change(label, before, layer)
        return path

    def record_change(self, label, before, layer=None):
        layer = layer if layer is not None else self.layer
        if layer.ExportToString() != before:
            self.undo.append((label, layer, before))
            self.undo = self.undo[-100:]
            self.redo.clear()

    def set_transform(self, data):
        from .usd_joint_paths import split_joint_path
        joint = split_joint_path(data["path"])
        if joint:
            from .usd_skeleton import write_joint
            if data.get("space", "local") != "local" or "matrix" in data.get("values", {}):
                raise ValueError("Skeleton joints use joint-local TRS values.")
            skeleton, name = joint
            return self.change("Joint transform " + name, lambda: write_joint(
                self.stage.GetPrimAtPath(skeleton), name, data["values"], data.get("frame", 0), data.get("time", "default")))
        from .usd_transforms import write_transform
        if "space" in data or "matrix" in data.get("values", {}):
            from .usd_transform_pose import write_pose
            return self.change("Transform " + data["path"], lambda: write_pose(
                self.stage.GetPrimAtPath(data["path"]), data["values"], data.get("frame", 0),
                data.get("time", "default"), data.get("space", "local"), data.get("representation", "trs")))
        return self.change("Transform " + data["path"], lambda: write_transform(
            self.stage.GetPrimAtPath(data["path"]), data["values"], data.get("frame", 0), data.get("time", "default")))

    def set_transform_lock(self, path, enabled):
        from .usd_transform_lock import set_locked
        return self.change(("Lock transforms " if enabled else "Unlock transforms ") + path,
                           lambda: set_locked(self.stage.GetPrimAtPath(path), enabled))

    def reparent_prim(self, data):
        from .usd_namespace import move_paths, move_prim, moved_load_rules
        source, destination = move_paths(self.stage, data["path"], data["parent"], data.get("name"))
        # NamespaceEditor can update specs and relationship targets in several
        # local layers. Keep the entire operation in one undo entry.
        layers = tuple(self.track_layer(layer) for layer in self.stage.GetLayerStack())
        before = tuple(layer.ExportToString() for layer in layers)
        previous_rules = self.stage.GetLoadRules()
        rules = moved_load_rules(self.stage, source, destination)
        try:
            path = move_prim(self.stage, data["path"], data["parent"], data["mode"], data.get("name"))
            self.stage.SetLoadRules(rules)
        except Exception:
            with Sdf.ChangeBlock():
                for layer, text in zip(layers, before):
                    if layer.ExportToString() != text:
                        layer.ImportFromString(text)
            self.stage.SetLoadRules(previous_rules)
            self.ensure_edit_target()
            raise
        changed = [(layer, text) for layer, text in zip(layers, before) if layer.ExportToString() != text]
        if changed:
            self.undo.append((("Rename " if "name" in data else "Reparent ") + str(source), tuple(p[0] for p in changed),
                              tuple(p[1] for p in changed), (str(source), str(destination))))
            self.undo = self.undo[-100:]
            self.redo.clear()
        return path

    def set_default_prim(self, path):
        prim = self.stage.GetPrimAtPath(path)
        if (not prim or prim.IsPseudoRoot() or prim.GetParent() != self.stage.GetPseudoRoot()
                or not prim.IsActive() or not prim.IsDefined() or prim.IsAbstract()):
            raise ValueError("Choose an active, defined prim at the stage root as the default prim.")
        def author():
            self.stage.SetDefaultPrim(prim)
            return str(prim.GetPath())
        # defaultPrim belongs to the root layer, independently of the edit target.
        return self.change("Set default prim " + str(prim.GetPath()), author, layer=self.stage.GetRootLayer())

    def duplicate_prim(self, path, mode):
        from .usd_duplicate import duplicate_prim
        layers = tuple(self.track_layer(layer) for layer in self.stage.GetLayerStack())
        before = tuple(layer.ExportToString() for layer in layers)
        rules = self.stage.GetLoadRules()
        try:
            result = duplicate_prim(self.stage, path, mode)
        except Exception:
            with Sdf.ChangeBlock():
                for layer, text in zip(layers, before):
                    if layer.ExportToString() != text:
                        layer.ImportFromString(text)
            self.stage.SetLoadRules(rules)
            self.ensure_edit_target()
            raise
        changed = [(layer, text) for layer, text in zip(layers, before) if layer.ExportToString() != text]
        if changed:
            self.undo.append(("Duplicate " + path, tuple(pair[0] for pair in changed),
                              tuple(pair[1] for pair in changed), {"load_rules": rules}))
            self.undo = self.undo[-100:]
            self.redo.clear()
        return result

    def set_prim_specifier(self, path, specifier):
        values = {"class": Sdf.SpecifierClass, "def": Sdf.SpecifierDef}
        if specifier not in values:
            raise ValueError("Choose class or def as the prim specifier.")
        prim = self.stage.GetPrimAtPath(path)
        if not editable_prim(prim) or prim.IsPseudoRoot():
            raise ValueError("Choose an editable prim outside instance contents.")
        value = values[specifier]
        if prim.GetSpecifier() == value:
            return path

        def author():
            if not prim.SetSpecifier(value):
                raise ValueError("USD could not change the prim's specifier.")
            if prim.GetSpecifier() != value:
                raise ValueError("The specifier is overridden. Choose a stronger edit target, such as the session layer.")
            return path

        return self.change(f"Convert {path} to {specifier}", author)

    def set_prim_active(self, path, active):
        return self.set_prims_active([path], active)

    def set_prims_active(self, paths, active):
        if type(active) is not bool:
            raise ValueError("Prim activation must be on or off.")
        return self._change_prim_activation(paths, active)

    def toggle_prims_active(self, paths):
        return self._change_prim_activation(paths, None)

    def _change_prim_activation(self, paths, active):
        if not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths):
            raise ValueError("Choose one or more prim paths to activate or deactivate.")
        paths = list(dict.fromkeys(paths))
        prims = [self.stage.GetPrimAtPath(path) for path in paths]
        if any(not activation_editable(prim) for prim in prims):
            raise ValueError("Choose a prim outside instance contents. The stage root and linked preview camera cannot be activated or deactivated.")
        changed = {path: not prim.IsActive() if active is None else active
                   for path, prim in zip(paths, prims) if active is None or prim.IsActive() != active}
        if not changed:
            return paths[-1]
        def author():
            # Activate first, then deactivate children before parents so all
            # targets remain composed while authoring. Roll back on any error.
            for path in sorted(changed, key=lambda path: (not changed[path], path.count("/") if changed[path] else -path.count("/"))):
                value = changed[path]
                if not self.stage.GetPrimAtPath(path).SetActive(value):
                    raise ValueError("USD could not change the prim's activation.")
                if self.stage.GetPrimAtPath(path).IsActive() != value:
                    raise ValueError("Activation is overridden. Choose a stronger edit target, such as the session layer.")
            return paths[-1]
        label = paths[0] if len(paths) == 1 else f"{len(paths)} prims"
        title = "Toggle activation of " if active is None else "Activate " if active else "Deactivate "
        return self.change(title + label, author)

    def set_frame_range(self, start, end):
        if any(type(value) is not int or not -(2 ** 31) <= value < 2 ** 31 for value in (start, end)):
            raise ValueError("Frame range endpoints must be 32-bit whole numbers.")
        if start > end:
            raise ValueError("Start frame must not be after end frame.")
        if (start, end) == (self.stage.GetStartTimeCode(), self.stage.GetEndTimeCode()):
            return
        # Stage time bounds compose from root/session metadata, not arbitrary
        # sublayers. Use the session so the range works with any selected target.
        layer = self.stage.GetSessionLayer()
        def author():
            with Usd.EditContext(self.stage, layer):
                self.stage.SetStartTimeCode(start)
                self.stage.SetEndTimeCode(end)
        self.change("Set frame range", author, layer=layer)

    def restore(self, redo=False):
        source, target = (self.redo, self.undo) if redo else (self.undo, self.redo)
        if source:
            label, layer, snapshot, *move = source[-1]
            if layer is None:
                current = tuple(self.stage.GetMutedLayers())
                self.restore_muted_layers(snapshot)
                source.pop()
                target.append((label, None, current))
                return
            layers = layer if isinstance(layer, tuple) else (layer,)
            snapshots = snapshot if isinstance(layer, tuple) else (snapshot,)
            if any(not item.permissionToEdit for item in layers):
                raise ValueError("The layer for this undo/redo no longer permits editing.")
            current = tuple(item.ExportToString() for item in layers)
            saved_rules = move[0].get("load_rules") if move and isinstance(move[0], dict) else None
            reverse = tuple(reversed(move[0])) if move and not isinstance(move[0], dict) else None
            previous_rules = self.stage.GetLoadRules()
            if reverse:
                from .usd_namespace import moved_load_rules
                rules = moved_load_rules(self.stage, *reverse)
            try:
                with Sdf.ChangeBlock():
                    for item, text in zip(layers, snapshots):
                        if not item.ImportFromString(text):
                            raise ValueError("USD could not restore the layer.")
                if reverse or saved_rules is not None:
                    self.stage.SetLoadRules(rules if reverse else saved_rules)
            except Exception:
                with Sdf.ChangeBlock():
                    for item, text in zip(layers, current):
                        item.ImportFromString(text)
                self.stage.SetLoadRules(previous_rules)
                raise
            source.pop()
            target.append((label, layer, current if isinstance(layer, tuple) else current[0],
                           *([reverse] if reverse else [{"load_rules": previous_rules}] if saved_rules is not None else [])))
            self.ensure_edit_target()
            return reverse

    def restore_muted_layers(self, identifiers):
        from .usd_layers import retain_layers
        retain_layers(self.stage, self)
        current, desired = set(self.stage.GetMutedLayers()), set(identifiers)
        self.stage.MuteAndUnmuteLayers(sorted(desired - current), sorted(current - desired))
        self.ensure_edit_target()

    def set_layer_muted(self, identifier, muted):
        from .usd_layers import layer_entries
        if type(muted) is not bool:
            raise ValueError("Layer mute state must be true or false.")
        entry = next((entry for entry in layer_entries(self.stage, self) if entry["identifier"] == identifier), None)
        if not entry or not entry["can_mute"]:
            raise ValueError("Choose a contributing or muted layer; the stage root cannot be muted.")
        before = tuple(self.stage.GetMutedLayers())
        desired = set(before)
        (desired.add if muted else desired.discard)(identifier)
        if desired == set(before):
            return
        self.restore_muted_layers(desired)
        self.undo.append((("Mute layer " if muted else "Unmute layer ") + entry["name"], None, before))
        self.undo = self.undo[-100:]
        self.redo.clear()

    def revert_layer(self, identifier):
        from .usd_layers import layer_entries
        entry = next((entry for entry in layer_entries(self.stage, self)
                      if entry["identifier"] == identifier), None)
        if entry is None:
            raise ValueError("This layer is no longer part of the open stage.")
        layer = self.layer_cache[identifier]
        if not layer.permissionToEdit:
            raise ValueError("This layer does not permit editing.")
        if layer not in self.initial:
            raise ValueError("This layer has no tracked edits to revert.")

        def restore_initial():
            with Sdf.ChangeBlock():
                if not layer.ImportFromString(self.initial[layer]):
                    raise ValueError("USD could not restore the layer's initial state.")

        if layer.ExportToString() != self.initial[layer]:
            self.change("Revert layer " + entry["name"], restore_initial, layer=layer)
        return entry["name"]

    def set_sublayers(self, data):
        from .usd_composition import local_layer, validate_sublayers, write_sublayers
        layer = local_layer(self.stage, data["identifier"])
        validate_sublayers(layer, data["sublayers"], data.get("expected"))
        return self.change("Edit sublayers of " + layer.GetDisplayName(),
                           lambda: write_sublayers(layer, data["sublayers"]), layer=layer)

    def create_sublayer(self, data):
        from .usd_composition import local_layer, new_sublayer, sublayer_entries, validate_sublayers, write_sublayers
        parent = local_layer(self.stage, data["identifier"])
        entries = sublayer_entries(parent)
        validate_sublayers(parent, entries, data.get("expected"))
        child, created_file = new_sublayer(data)
        try:
            path = child.identifier
            if created_file and parent.realPath and Path(parent.realPath).is_file():
                path = Path(os.path.relpath(created_file, Path(parent.realPath).parent)).as_posix()
            entries.append(dict(path=path, offset=0, scale=1))
            # Hold anonymous layers through removal/Undo so Redo restores the
            # same layer and its contents, rather than an unresolved anon ID.
            self.track_layer(child)
            self.change("Create sublayer " + child.GetDisplayName(),
                        lambda: write_sublayers(parent, entries), layer=parent)
        except Exception:
            self.saved.pop(child, None)
            self.initial.pop(child, None)
            if created_file:
                created_file.unlink(missing_ok=True)
            raise
        return child.identifier

    def add_arc(self, data):
        from .usd_composition import prepare_arc, write_arc
        prim, asset, path = prepare_arc(self.stage, data)
        return self.change("Add " + data["kind"] + " to " + data["path"],
                           lambda: write_arc(prim, data["kind"], asset, path))

    def set_property(self, data):
        prim = self.stage.GetPrimAtPath(data["path"])
        if prim and prim.IsPseudoRoot() and data["group"] == "Metadata":
            return self.set_stage_metadata({data["name"]: data["value"]})
        frame = data.get("frame", 0)
        if type(frame) not in (int, float) or not math.isfinite(frame):
            raise ValueError("Frame must be a finite number.")
        info = property_info(prim, data["group"], data["name"], Usd.TimeCode(frame))
        value = data["value"]
        group, name = data["group"], data["name"]
        if group == "Attributes":
            attr = prim.GetAttribute(name)
            value = decode_value(attr.GetTypeName(), value)
            if info["options"] and value not in info["options"]:
                raise ValueError("Choose one of the allowed token values.")
            mode = data.get("time", "default")
            if mode not in ("default", "frame"):
                raise ValueError("Choose Default value or Time sample.")
            if mode == "frame" and info["uniform"]:
                raise ValueError("Uniform attributes cannot have time samples.")
            time = Usd.TimeCode(frame) if mode == "frame" else Usd.TimeCode.Default()
            # A stronger layer's time samples replace the weaker sample map.
            # Copy resolved samples before inserting one, preserving animation.
            samples = [(t, attr.Get(t)) for t in attr.GetTimeSamples()] if mode == "frame" else []
            def action():
                for t, original in samples:
                    if not attr.Set(Sdf.ValueBlock() if original is None else original, Usd.TimeCode(t)):
                        return False
                return attr.Set(value, time)
        elif group == "Relationships":
            if not isinstance(value, list) or any(not isinstance(p, str) for p in value):
                raise ValueError('Use a JSON list of target paths, such as ["/World/Material"].')
            paths = [Sdf.Path(p) for p in value]
            if any(not p.IsAbsolutePath() or not (p.IsPrimPath() or p.IsPropertyPath()) for p in paths):
                raise ValueError("Targets must be absolute prim or property paths.")
            action = lambda: prim.GetRelationship(name).SetTargets(paths)
        else:
            if type(value) is not type(info["value"]):
                raise ValueError("The metadata value has the wrong type.")
            action = lambda: prim.SetMetadata(name, value)
        def apply():
            if not action():
                raise ValueError("USD could not author this property.")
            return str(prim.GetPath())
        label = "Edit " + name
        if group == "Attributes" and mode == "frame":
            label += f" at frame {frame:g}"
        return self.change(label, apply)

    def set_stage_metadata(self, values):
        from .usd_stage_metadata import edit_stage_metadata
        return edit_stage_metadata(self, values)

    def add_prim(self, parent_path, name, kind):
        parent = self.stage.GetPrimAtPath(parent_path)
        if not editable_prim(parent, children=True):
            raise ValueError("Cannot add children inside an instance or an unavailable prim.")
        if kind not in PRIM_TYPES or not Sdf.Path.IsValidIdentifier(name):
            raise ValueError("Use a prim name containing letters, digits, and underscores, starting with a letter or underscore.")
        path = parent.GetPath().AppendChild(name)
        if self.stage.GetPrimAtPath(path):
            raise ValueError("A prim with this name already exists.")
        def apply():
            prim = self.stage.DefinePrim(path, "Mesh" if kind == "Plane" else kind)
            if kind == "Plane":
                mesh = UsdGeom.Mesh(prim)
                points = ([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)]
                          if UsdGeom.GetStageUpAxis(self.stage) == UsdGeom.Tokens.z else
                          [(-1, 0, -1), (-1, 0, 1), (1, 0, 1), (1, 0, -1)])
                mesh.CreatePointsAttr(points)
                mesh.CreateFaceVertexCountsAttr([4])
                mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
                mesh.CreateSubdivisionSchemeAttr("none")
            xform = UsdGeom.Xformable(prim)
            if xform:
                xform.AddTranslateOp(UsdGeom.XformOp.PrecisionFloat).Set(Gf.Vec3f(0))
                xform.AddOrientOp(UsdGeom.XformOp.PrecisionFloat).Set(Gf.Quatf(1))
                xform.AddScaleOp().Set(Gf.Vec3f(1))
            return str(path)
        return self.change("Add " + name, apply)

    def remove_prim(self, path):
        prim = self.stage.GetPrimAtPath(path)
        if not editable_prim(prim) or prim.IsPseudoRoot():
            raise ValueError("Cannot remove the stage root or an instance's contents.")
        def apply():
            self.stage.RemovePrim(path)
            remaining = self.stage.GetPrimAtPath(path)
            if remaining:
                # A weaker/reference layer still defines it. Deactivate in the
                # edit target to remove it from the composed scene.
                remaining.SetActive(False)
            return str(Sdf.Path(path).GetParentPath())
        return self.change("Remove " + prim.GetName(), apply)

    def convert_to_mesh(self, path):
        prim = self.stage.GetPrimAtPath(path)
        if not editable_prim(prim) or prim.IsInstance() or not prim.IsA(UsdGeom.Cube):
            raise ValueError("Select an editable Cube prim to convert to a mesh.")
        size = UsdGeom.Cube(prim).GetSizeAttr()
        # Match OpenUSD's cube adapter, preserving indexed primvars and face order.
        corners = ((1, 1, 1), (-1, 1, 1), (-1, -1, 1), (1, -1, 1),
                   (-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1))
        indices = [0, 1, 2, 3, 4, 5, 6, 7, 0, 6, 5, 1,
                   4, 7, 3, 2, 0, 3, 7, 6, 4, 2, 1, 5]
        samples = []
        for time in [Usd.TimeCode.Default(), *map(Usd.TimeCode, size.GetTimeSamples())]:
            length = size.Get(time)
            if length is None or not math.isfinite(length):
                raise ValueError("Cube size must have finite values before conversion.")
            points = [Gf.Vec3f(*(v * length / 2 for v in corner)) for corner in corners]
            if any(not math.isfinite(v) for point in points for v in point):
                raise ValueError("Cube size exceeds the mesh point precision.")
            samples.append((time, points))

        def apply():
            # Override the type at the same path in the edit target. Transforms,
            # bindings, children and other composed opinions remain on this prim.
            mesh = UsdGeom.Mesh(self.stage.DefinePrim(path, "Mesh"))
            mesh.CreateFaceVertexCountsAttr([4] * 6)
            mesh.CreateFaceVertexIndicesAttr(indices)
            mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.bilinear)
            for time, points in samples:
                if not mesh.CreatePointsAttr().Set(points, time) or not mesh.CreateExtentAttr().Set(UsdGeom.Mesh.ComputeExtent(points), time):
                    raise ValueError("USD could not author the converted cube geometry.")
            return str(prim.GetPath())
        return self.change("Convert cube to mesh", apply)

    def apply_schema(self, path, schema, instance=""):
        prim = self.stage.GetPrimAtPath(path)
        if not editable_prim(prim) or prim.IsPseudoRoot():
            raise ValueError("Select an editable prim to apply a schema.")
        def apply():
            apply_schema(prim, schema, instance)
            return str(prim.GetPath())
        return self.change("Apply " + schema + (":" + instance if instance else ""), apply)

    def bind_material(self, path, graph, base_dir):
        from .usd_materials import bind_graph_materials, can_bind_material
        paths = list(dict.fromkeys([path] if isinstance(path, str) else path))
        if not paths:
            raise ValueError("Select one or more editable prims to bind a material.")
        prims = [self.stage.GetPrimAtPath(value) for value in paths]
        for value, prim in zip(paths, prims):
            if not editable_prim(prim) or not can_bind_material(prim):
                raise ValueError("Cannot bind a material to " + value + ". Select editable geometry, transforms/scopes or mesh face subsets.")
        return self.change("Bind material from shader graph", lambda: bind_graph_materials(self.stage, prims, graph, base_dir))

    def bind_scene_material(self, material, paths):
        from .usd_material_bindings import bind_existing_material
        return self.change("Apply scene material", lambda: bind_existing_material(self.stage, material, paths))

    def apply_light_graph(self, data, frame=0):
        from .usd_light_graph import apply_light_graph
        return self.change("Apply light graph", lambda: apply_light_graph(self.stage, data, frame))

    def apply_shader_graph(self, data):
        from .usd_shader_graph import apply_graph
        from .usd_material_sync import store_link
        def apply():
            link = apply_graph(self.stage, data)
            if link["fingerprint"] != data["link"]["fingerprint"]:
                store_link(self.stage, data["graph"], link)
            return link
        return self.change("Apply material graph", apply)

    def sync_materials(self, updates, preview_camera=None, *, derived=False):
        from .usd_material_sync import apply_updates
        def apply():
            records = apply_updates(self.stage, updates)
            if preview_camera:
                from .usd_preview_camera import apply_update
                apply_update(self.stage, preview_camera)
            return records
        # Evaluated camera values are derived from the scene. Keep source-camera
        # Undo/Redo reachable, while retaining normal rollback and dirty tracking.
        return self.change("Sync materials and preview camera" if preview_camera else "Sync materials", apply, record=not derived)

    def set_preview_camera_lock(self, enabled):
        from .usd_preview_camera import set_locked
        return self.change("Lock preview camera" if enabled else "Unlock preview camera", lambda: set_locked(self.stage, enabled))

    def create_mesh_light(self, path):
        mesh = self.stage.GetPrimAtPath(path)
        if not editable_prim(mesh) or mesh.IsInstance() or not mesh.IsA(UsdGeom.Mesh):
            raise ValueError("Select an editable Mesh. Convert a Cube to a mesh first.")
        target = mesh.GetPath().GetParentPath().AppendChild(mesh.GetName() + "_Light")
        while self.stage.GetPrimAtPath(target):
            target = Sdf.Path(str(target) + "_")
        def apply():
            prim = self.stage.DefinePrim(target, "MoonrayMeshLight")
            light = UsdLux.LightAPI.Apply(prim)
            light.CreateColorAttr(Gf.Vec3f(1))
            light.CreateIntensityAttr(1.0)
            light.CreateNormalizeAttr(False)
            prim.CreateRelationship("inputs:geometry", custom=False).SetTargets([mesh.GetPath()])
            # MoonRay builds its own emitter layer. A mesh in the regular layer
            # is rejected, even when its geometry relationship is correct.
            visibility = UsdGeom.Imageable(mesh).CreateVisibilityAttr(UsdGeom.Tokens.invisible)
            for time in visibility.GetTimeSamples():
                visibility.Set(UsdGeom.Tokens.invisible, time)
            return str(target)
        return self.change("Create mesh light", apply)

    def export(self, path, retained=(), *, flattened=False):
        path = Path(path).expanduser().absolute()
        if path.suffix.lower() not in (".usd", ".usda", ".usdc"):
            raise ValueError("Save as .usd, .usda, or .usdc.")
        if not flattened:
            from .usd_export import save_composition
            save_composition(self.stage, path, retained)
            self.saved = {layer: layer.ExportToString() for layer in self.saved}
            return str(path)
        # Replace only after USD has successfully written the complete stage.
        fd, temporary = tempfile.mkstemp(prefix=".usd-save-", suffix=path.suffix, dir=path.parent)
        os.close(fd)
        try:
            from .usd_export import flatten_for_save
            if not flatten_for_save(self.stage).Export(temporary):
                raise ValueError("USD could not save the composed stage.")
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return str(path)
