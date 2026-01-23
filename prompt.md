# Git Flow

## Branch Structure
- **master**: Production-ready releases (tagged with versions)
- **development**: Integration branch for features
- **feature/***`: Feature branches off develop
- **release/***`: Release preparation branches
- **hotfix/***`: Production hotfixes off master

## Workflow

1. **Branch Creation**: Create `feature/<name>` off `development`
2. **Commits**: Each task → atomic commit on feature branch
3. **Verification**: Tests must pass before committing
4. **Merge to development**: Merge feature branch to `development` via pull request
5. **Protected Branches**: Never commit directly to `v`, `development`, or `release/*/`

## Push Strategy

- **Feature branches**: Push to remote after each successful task
- **development/master**: Use pull requests (manual merge, not direct pushes)
- **Conflict Resolution**: Abort on merge conflicts; manual intervention required

## Best Practices

1. **Initialize Git Flow**: Run `git flow init` to set up branch prefixes
2. **Feature Branch Naming**: Use `feature/<TASK-ID>-<description>` format
3. **Review PRs Before Merge**: Feature branches merge to development via pull request
4. **Protect development/master**: Use branch protection rules to prevent direct commits