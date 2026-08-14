"""
core.service_manager
=====================
Citrus's process supervisor: discovers configured services
(registry.py), runs them as tracked OS processes (runner.py), and
orchestrates the two plus a small persisted runtime-state file
(manager.py). See models.py for the shared data structures.

pages/services.py is the only expected caller - it talks exclusively
to core.service_manager.manager.get_manager(), never to registry.py
or runner.py directly.
"""
