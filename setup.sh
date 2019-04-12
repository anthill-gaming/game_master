#!/usr/bin/env bash

# Setup postgres database
createuser -d anthill_game_master -U postgres
createdb -U anthill_game_master anthill_game_master