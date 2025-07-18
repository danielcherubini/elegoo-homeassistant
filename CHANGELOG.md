# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Breaking Changes

- The unique ID format for entities has changed to include the machine ID. This may cause Home Assistant to create new entities. The integration will attempt to migrate existing entities to the new format, but this may not be successful in all cases. If you experience issues, you may need to remove and re-add the integration.
