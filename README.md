# github-syncer

Utility to backup your GitHub repositories locally

## Features

## Example usage

## TODO

- Resolving `pull` errors (i.e. after `push --force`)
- Private repos support
- Docker support
- Examples for real world

## Q&A

1. Why not to use `Event`s in GitHub Rest API?

`/users/{user}/events` is not that flexible: it returns *set* of events occurred *in some* period.
It is not event streaming - if you make 2 consecutive HTTP requests you will get same set of events.
And more importantly, it can have a long delay before new event appears.
That is why good old polling works quite well, even though it can do extra work and consume some more resources.
