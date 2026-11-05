#!/bin/bash

pnpm -w install
pnpm prisma migrate dev
pnpm run dev
