"""The GUI resource cache is built by two processes at once.

`GUIInterface._cache_gui_files` runs on every `GUIInterface.__init__`, so on
every skill load that carries GUI resources, and the path it builds,
`$XDG_CACHE_HOME/ovos_gui/<skill_id>`, carries the skill id only. Two
processes that load one skill id under one `XDG_CACHE_HOME` therefore build
one directory. pytest-xdist workers are exactly that shape.

The probe below is the shape that measured the defect (ci, T-5644): two
processes, one skill id, one cache root, many builds each. Against a
destroy-then-refill implementation it fails about one build in thirteen, with
`FileNotFoundError` on the destination framework directory or `shutil.Error`
listing each file that vanished under the copy. One green run of a suite is
not evidence here; the count over many builds is.
"""
import multiprocessing
import os
import shutil
import stat
import tempfile
import unittest

# two processes, this many cache builds each, this many rounds. 2 x 20 x 2 is
# 80 builds, where the unfixed implementation fails about six times.
WORKERS = 2
BUILDS = 20
ROUNDS = 2

FRAMEWORK_FILES = {"qt5": ("Main.qml", "Second.qml", "Third.qml"),
                   "all": ("shared.png",)}


def _make_resource_dirs(root):
    """Write the resource directories a skill would ship."""
    ui_directories = {}
    for framework, names in FRAMEWORK_FILES.items():
        bpath = os.path.join(root, "res", framework)
        os.makedirs(bpath, exist_ok=True)
        for name in names:
            with open(os.path.join(bpath, name), "w") as f:
                f.write(f"{framework}/{name}\n" * 200)
        ui_directories[framework] = bpath
    return ui_directories


def _expected_files():
    """The paths the served tree must hold after a build."""
    expected = set()
    for framework, names in FRAMEWORK_FILES.items():
        for name in names:
            if framework == "all":
                expected.add(name)
            else:
                expected.add(f"{framework}/{name}")
    return expected


def _snapshot(served):
    """Read the served tree once, and say whether it is whole.

    A reader of the cache sees one of two states: the tree is not there, or it
    is there and complete. A listing that holds some of a framework's files
    and not the rest is the defect, so it is reported rather than retried.
    """
    try:
        root = set(os.listdir(served))
        frameworks = {name: set(os.listdir(os.path.join(served, name)))
                      for name in FRAMEWORK_FILES if name != "all"}
    except FileNotFoundError:
        return None  # the tree is between two versions of itself
    if root < set(FRAMEWORK_FILES["all"]):
        return f"root holds {sorted(root)}"
    for name, files in frameworks.items():
        if files != set(FRAMEWORK_FILES[name]):
            return f"{name} holds {sorted(files)}"
    return ""


def _worker(cache_home, ui_directories, skill_id, builds, queue):
    """Build the cache `builds` times and report what went wrong."""
    os.environ["XDG_CACHE_HOME"] = cache_home
    failures = []
    partial = []
    absent = 0
    from ovos_bus_client.apis.gui import GUIInterface
    served = os.path.join(cache_home, "ovos_gui", skill_id)
    for _ in range(builds):
        try:
            GUIInterface(skill_id, bus=None, config={},
                         ui_directories=dict(ui_directories))
        except Exception as e:
            failures.append(f"{type(e).__name__}: {e}")
            continue
        state = _snapshot(served)
        if state is None:
            absent += 1
        elif state:
            partial.append(state)
    queue.put((builds, failures, partial, absent))


class TestConcurrentCacheBuild(unittest.TestCase):
    """Two processes build one skill id's cache under one cache root."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gui-cache-race-")
        self.ui_directories = _make_resource_dirs(self.root)
        self.cache_home = os.path.join(self.root, "cache")
        os.makedirs(self.cache_home, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_round(self, skill_id):
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        procs = [ctx.Process(target=_worker,
                             args=(self.cache_home, self.ui_directories,
                                   skill_id, BUILDS, queue))
                 for _ in range(WORKERS)]
        for p in procs:
            p.start()
        results = [queue.get(timeout=300) for _ in procs]
        for p in procs:
            p.join(timeout=60)
            self.assertFalse(p.is_alive(), "a cache builder did not finish")
        return results

    def test_no_build_fails_and_no_reader_sees_a_partial_tree(self):
        built = 0
        failures = []
        partial = []
        for round_n in range(ROUNDS):
            skill_id = f"race-skill-{round_n}.test"
            for count, fails, seen, _absent in self._run_round(skill_id):
                built += count
                failures.extend(fails)
                partial.extend(seen)
            # quiescent, so the tree the last writer published is complete
            served = os.path.join(self.cache_home, "ovos_gui", skill_id)
            for path in sorted(_expected_files()):
                self.assertTrue(os.path.isfile(os.path.join(served, path)),
                                path)
        self.assertEqual(built, WORKERS * BUILDS * ROUNDS)
        # the count is the finding: report every failure, not the first
        self.assertEqual(
            failures, [],
            f"{len(failures)} of {built} cache builds raised: {failures}")
        self.assertEqual(
            partial, [],
            f"{len(partial)} of {built} builds left a reader a tree that "
            f"holds some of a framework's files and not the rest: {partial}")

    def test_the_staging_directories_do_not_survive(self):
        """A build leaves the served tree and nothing beside it.

        The staged tree is built beside the target, so a leaked staging
        directory would grow the cache root on every skill load.
        """
        self._run_round("leftover-skill.test")
        cache_root = os.path.join(self.cache_home, "ovos_gui")
        self.assertEqual(sorted(os.listdir(cache_root)),
                         ["leftover-skill.test"])


class TestSingleProcessBuildIsUnchanged(unittest.TestCase):
    """The served layout is what it was: frameworks in their own directories,
    the "all" tree at the root."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gui-cache-layout-")
        self.ui_directories = _make_resource_dirs(self.root)
        self.cache_home = os.path.join(self.root, "cache")
        os.environ["XDG_CACHE_HOME"] = self.cache_home

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        os.environ.pop("XDG_CACHE_HOME", None)

    def test_the_served_layout(self):
        from ovos_bus_client.apis.gui import GUIInterface
        skill_id = "layout-skill.test"
        GUIInterface(skill_id, bus=None, config={},
                     ui_directories=dict(self.ui_directories))
        served = os.path.join(self.cache_home, "ovos_gui", skill_id)
        for path in sorted(_expected_files()):
            self.assertTrue(os.path.isfile(os.path.join(served, path)), path)

    def test_a_second_build_replaces_the_tree(self):
        """A resource that the skill dropped does not survive a rebuild."""
        from ovos_bus_client.apis.gui import GUIInterface
        skill_id = "rebuild-skill.test"
        GUIInterface(skill_id, bus=None, config={},
                     ui_directories=dict(self.ui_directories))
        served = os.path.join(self.cache_home, "ovos_gui", skill_id)
        os.remove(os.path.join(self.ui_directories["qt5"], "Third.qml"))
        GUIInterface(skill_id, bus=None, config={},
                     ui_directories=dict(self.ui_directories))
        self.assertFalse(os.path.exists(os.path.join(served, "qt5",
                                                     "Third.qml")))
        self.assertTrue(os.path.isfile(os.path.join(served, "qt5",
                                                    "Main.qml")))

    def test_a_stale_file_at_the_served_path_is_replaced(self):
        """The served path holding a file, not a directory, is recovered."""
        from ovos_bus_client.apis.gui import GUIInterface
        skill_id = "stale-file-skill.test"
        cache_root = os.path.join(self.cache_home, "ovos_gui")
        os.makedirs(cache_root, exist_ok=True)
        with open(os.path.join(cache_root, skill_id), "w") as f:
            f.write("not a directory\n")
        GUIInterface(skill_id, bus=None, config={},
                     ui_directories=dict(self.ui_directories))
        served = os.path.join(cache_root, skill_id)
        self.assertTrue(os.path.isfile(os.path.join(served, "qt5",
                                                    "Main.qml")))


def _mode_worker(root, skill_id, with_all, umask, all_mode, rebuild, queue):
    """Build one skill's cache in a fresh process and report the modes.

    The umask is process-global. The code under test says that reading it
    requires setting it, and that setting it races every other thread in the
    process; that is as true of a test as of the fix, and a suite that counts
    log records runs in this process too. So each case gets its own process,
    which is also where `XDG_CACHE_HOME` and the ovos `LOG` initialisation
    stay.
    """
    try:
        os.umask(umask)
        case = os.path.join(root, skill_id)
        cache_home = os.path.join(case, "cache")
        os.environ["XDG_CACHE_HOME"] = cache_home
        ui = {}
        bpath = os.path.join(case, "res", "qt5")
        os.makedirs(bpath)
        with open(os.path.join(bpath, "Main.qml"), "w") as f:
            f.write("x\n")
        ui["qt5"] = bpath
        if with_all:
            shared = os.path.join(case, "res", "all")
            os.makedirs(shared)
            with open(os.path.join(shared, "shared.png"), "w") as f:
                f.write("y\n")
            if all_mode is not None:
                os.chmod(shared, all_mode)
            ui["all"] = shared
        from ovos_bus_client.apis.gui import GUIInterface

        GUIInterface(skill_id, bus=None, config={}, ui_directories=ui)
        cache_root = os.path.join(cache_home, "ovos_gui")
        served = os.path.join(cache_root, skill_id)
        result = {"cache_root": stat.S_IMODE(os.stat(cache_root).st_mode),
                  "served": stat.S_IMODE(os.stat(served).st_mode)}
        if rebuild:
            GUIInterface(skill_id, bus=None, config={}, ui_directories=ui)
            result["rebuilt"] = stat.S_IMODE(os.stat(served).st_mode)
        queue.put(result)
    except Exception as e:
        queue.put({"error": f"{type(e).__name__}: {e}"})


class TestTheServedTreeKeepsItsPermissions(unittest.TestCase):
    """The published directory must be traversable by the GUI service.

    `tempfile.mkdtemp` creates its directory 0700 by design and `os.rename`
    carries the mode with the inode, so building the tree in a staging
    directory published 0700 where the tree built in place was 0755. Only the
    top directory changed, and that is enough: traversal needs the execute bit,
    so every file below is unreachable to another user whatever its own mode.
    `served/<framework>` staying 0755 is `copytree` preserving the source mode,
    which hides the change from a casual look (reviewer-b, T-6294).

    The condition matters and is asserted both ways. `shutil.copytree` copies
    the SOURCE directory's mode onto an existing destination, so the `all`
    framework, which copies onto the staging directory itself, used to overwrite
    mkdtemp's 0700 while a skill with only named frameworks kept it. A skill
    that ships no `all` directory is the ordinary case.

    The rule the fix implements: publish with the mode of the directory that
    CONTAINS the tree, which is what `makedirs` gave the cache root under this
    process's umask, so the fix reproduces what the earlier tree served without
    reading the umask.

    What that tree served has two cases, and the chmod only decides the first.
    Where the skill ships no `all` directory, or ships one whose mode matches the
    umask, the served directory's mode equalled the cache root's (022 -> 0755,
    077 -> 0700, 002 -> 0775). Where an `all` source's mode differs, the
    copystat named in the paragraph above puts that SOURCE's mode on the served
    directory instead: measured on the earlier tree, umask 077 with an `all`
    source at 0755 served 0755 from a 0700 cache root, and umask 022 with an
    `all` source at 0700 served 0700 from a 0755 cache root. A packaged skill is
    the second case, because resources installed by pip carry 0755 whatever the
    running umask is. This head reproduces both, and
    `test_a_private_all_directory_still_publishes_its_own_mode` is the case that
    holds the second one.

    Every case here runs in a spawned process, because the umask it needs is
    process-global (reviewer-b, T-6401: setting it in the pytest process made
    log-record-counting tests elsewhere in the suite red about one run in
    three).
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gui-cache-mode-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _build(self, skill_id, with_all, umask=0o022, all_mode=None,
               rebuild=False):
        """Run one case in its own process and return the modes it read."""
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        proc = ctx.Process(target=_mode_worker,
                           args=(self.root, skill_id, with_all, umask,
                                 all_mode, rebuild, queue))
        proc.start()
        try:
            result = queue.get(timeout=300)
        finally:
            proc.join(timeout=60)
        self.assertFalse(proc.is_alive(), "the mode probe did not finish")
        self.assertNotIn("error", result, result.get("error"))
        return result

    def test_a_skill_with_no_all_directory_is_still_traversable(self):
        """The case that regressed: 0700, so no other user could enter it."""
        r = self._build("mode-no-all.test", with_all=False)
        self.assertEqual(oct(r["served"]), oct(0o755))
        self.assertEqual(oct(r["served"]), oct(r["cache_root"]))

    def test_a_skill_with_an_all_directory_is_unchanged(self):
        """The case that never regressed, asserted so the fix cannot move it."""
        r = self._build("mode-with-all.test", with_all=True)
        self.assertEqual(oct(r["served"]), oct(0o755))
        self.assertEqual(oct(r["served"]), oct(r["cache_root"]))

    def test_a_private_all_directory_still_publishes_its_own_mode(self):
        """dev's behaviour, pinned: `copytree` copies the `all` source's mode
        onto the staging directory, so a skill that ships a 0700 `all`
        directory serves a 0700 tree even where the cache root is 0755. The fix
        sets the staging mode BEFORE the copies, which keeps that. This is the
        case that fails if the `chmod` ever moves after the copy loop
        (reviewer-b, T-6401: served became 0755 in that counterfactual)."""
        r = self._build("mode-private-all.test", with_all=True,
                        all_mode=0o700)
        self.assertEqual(oct(r["cache_root"]), oct(0o755))
        self.assertEqual(oct(r["served"]), oct(0o700))

    def test_the_execute_bit_is_what_this_is_about(self):
        """Said as the consequence rather than as a number: another user must
        be able to traverse into the served directory and read a file."""
        r = self._build("mode-traversal.test", with_all=False)
        served = r["served"]
        self.assertTrue(served & stat.S_IXOTH, oct(served))
        self.assertTrue(served & stat.S_IROTH, oct(served))

    def test_a_tighter_umask_is_honoured_and_not_overridden(self):
        """The fix must not hardcode 0755: a deployment that sets a private
        umask gets a private cache, as it did before the staging change."""
        r = self._build("mode-tight-umask.test", with_all=False, umask=0o077)
        self.assertEqual(oct(r["cache_root"]), oct(0o700))
        self.assertEqual(oct(r["served"]), oct(r["cache_root"]))

    def test_a_looser_umask_is_honoured_too(self):
        r = self._build("mode-loose-umask.test", with_all=False, umask=0o002)
        self.assertEqual(oct(r["cache_root"]), oct(0o775))
        self.assertEqual(oct(r["served"]), oct(r["cache_root"]))

    def test_a_rebuild_does_not_tighten_the_mode(self):
        """The second build takes the same path, so it must publish the same
        mode: a skill that reloads must not lock its own cache away."""
        r = self._build("mode-rebuild.test", with_all=False, rebuild=True)
        self.assertEqual(oct(r["rebuilt"]), oct(r["served"]))
        self.assertEqual(oct(r["served"]), oct(r["cache_root"]))


if __name__ == "__main__":
    unittest.main()
