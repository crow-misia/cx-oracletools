"""Defines classes for describing objects."""

import os
import sys

import Object
import Statements
import Utils

__all__ = [ "Describer", "Exporter", "FileNameForObject" ]

class Describer(object):
    """Base class for describing objects."""

    def __init__(self, environment, options, outFile = None):
        self.environment = environment
        self.wantGrants = True
        self.wantComments = True
        self.wantTablespace = True
        self.wantStorage = True
        self.wantSequenceValues = False
        self.wantRelated = False
        self.wantForeignKeys = False
        self.wantTriggers = False
        self.mergeGrants = True
        self.nameOnly = False
        self.includeRoles = False
        self.includeUsers = False
        self.includedObjects = None
        self.onlyIf = None
        self.objectTypes = []
        self.schemas = []
        if outFile is None:
            self.outFile = sys.stdout
        else:
            self.outFile = outFile
        Utils.SetOptions(self, options)
        if not self.schemas:
            self.schemas = [environment.connection.username.upper()]

    def __FetchObjectsToInclude(self):
        """Populate the dictionary of objects to include in the export."""
        whereClause = "where owner %s and %s" % \
                (self.schemasClause, self.onlyIf)
        if self.objectTypes:
            clauses = ["'%s'" % t for t in self.objectTypes]
            whereClause += " and object_type in (%s)" % ",".join(clauses)
        sql = "select owner, object_name from %s_objects %s" % \
                (self.environment.ViewPrefix(), whereClause)
        cursor, isPrepared = self.environment.Cursor()
        cursor.execute(sql)
        self.includedObjects = dict.fromkeys(cursor)

    def __GetObjectTypes(self):
        return self.__objectTypes

    def __GetSchemas(self):
        return self.__schemas

    def __SetObjectTypes(self, objectTypes):
        self.__objectTypes = [s.replace("_", " ").upper() for s in objectTypes]

    def __SetSchemas(self, schemas):
        self.__schemas = [s.upper() for s in schemas]
        self.__schemas.sort()
        if len(self.__schemas) == 1:
            self.currentOwner, = self.__schemas
            self.schemasClause = "= '%s'" % self.currentOwner
        else:
            self.currentOwner = None
            schemas = ["'%s'" % s for s in self.schemas]
            self.schemasClause = "in (%s)" % ",".join(schemas)

    objectTypes = property(__GetObjectTypes, __SetObjectTypes, None,
            "the list of object types to describe")

    schemas = property(__GetSchemas, __SetSchemas, None,
            "the list of schemas to describe")

    def ExportAllObjects(self):
        """Export all objects for the chosen schemas."""
        if self.onlyIf is not None:
            self.__FetchObjectsToInclude()
        if self.includeRoles:
            self.ExportRoles()
        if self.includeUsers:
            self.ExportUsers()
        if self.TypeIncluded("SYNONYM"):
            self.ExportSynonyms()
        if self.TypeIncluded("SEQUENCE"):
            self.ExportSequences()
        if self.TypeIncluded("TABLE"):
            self.ExportTables()
            if not self.wantRelated:
                self.ExportConstraints()
                self.ExportIndexes()
        if self.TypeIncluded("VIEW"):
            self.ExportViews()
        if self.SourceTypes():
            self.ExportSource()
        if not self.wantTriggers and self.TypeIncluded("TRIGGER"):
            self.ExportTriggers()

    def ExportConstraints(self):
        """Export all of the constraints."""
        print >> sys.stderr, "Describing constraints..."
        self.ExportObjects(Object.ObjectIterator(self.environment,
                "AllConstraints", Statements.CONSTRAINTS,
                "where o.owner %s" % self.schemasClause, Object.Constraint))

    def ExportIndexes(self):
        """Export all of the indexes."""
        print >> sys.stderr, "Describing indexes..."
        self.ExportObjects(Object.ObjectIterator(self.environment,
                "AllIndexes", Statements.INDEXES,
                "where o.owner %s" % self.schemasClause, Object.Index))

    def ExportObject(self, object):
        """Exports the object to the output."""
        if isinstance(object, Object.Sequence):
            object.Export(self.outFile, self.wantSequenceValues)
        elif isinstance(object, (Object.ObjectWithStorage, Object.Constraint)):
            object.Export(self.outFile, self.wantTablespace, self.wantStorage)
        else:
            object.Export(self.outFile)
        if self.wantGrants and isinstance(object, Object.ObjectWithPrivileges):
            object.ExportPrivileges(self.outFile, self.mergeGrants)
        if self.wantComments and isinstance(object, Object.ObjectWithComments):
            object.ExportComments(self.outFile)
        if self.wantRelated:
            if isinstance(object, Object.StoredProcWithBody):
                body = object.Body()
                if body:
                    self.ExportObject(body)
            if isinstance(object, Object.Table):
                for constraint in object.Constraints():
                    self.ExportObject(constraint)
                for index in object.Indexes():
                    self.ExportObject(index)
        if self.wantTriggers and isinstance(object, Object.ObjectWithTriggers):
            for trigger in object.Triggers():
                self.ExportObject(trigger)
        if self.wantForeignKeys and isinstance(object, Object.Table):
            for constraint in object.ReferencedConstraints():
                self.ExportObject(constraint)

    def ExportObjects(self, sequence):
        """Export all the objects from the interator (or sequence)."""
        for obj in sequence:
            if not self.ObjectIncluded(obj):
                continue
            self.SetOwner(obj.owner, obj.type)
            self.ExportObject(obj)

    def ExportRoles(self):
        """Export all roles granted with admin option to schemas exported."""
        print >> sys.stderr, "Describing roles..."
        whereClause = "where o.role in (select granted_role " + \
                "from dba_role_privs where admin_option = 'YES' " + \
                "and grantee %s)" % self.schemasClause
        self.ExportObjects(Object.ObjectIterator(self.environment, "AllRoles",
                Statements.ROLES, whereClause, Object.Role))

    def ExportSequences(self):
        """Export all of the sequences."""
        print >> sys.stderr, "Describing sequences..."
        self.ExportObjects(Object.ObjectIterator(self.environment,
                "AllSequences", Statements.SEQUENCES,
                "where o.sequence_owner %s" % self.schemasClause,
                Object.Sequence))

    def ExportSource(self):
        """Exports all source objects (in correct dependency order)."""
        print >> sys.stderr, "Retrieving interdependent objects..."
        objects = self.RetrieveSourceObjects()
        print >> sys.stderr, "Retrieving dependencies..."
        dependencies = self.RetrieveDependencies()
        print >> sys.stderr, len(objects),
        print >> sys.stderr, "interdependent objects to describe..."
        for owner, name, type in Utils.OrderObjects(objects, dependencies):
            self.RetrieveAndExportObject(owner, name, type)

    def ExportSynonyms(self):
        """Export all of the synonyms."""
        print >> sys.stderr, "Describing synonyms..."
        whereClause = "where o.owner %s or o.owner = 'PUBLIC' " + \
                      "and o.table_owner %s"
        self.ExportObjects(Object.ObjectIterator(self.environment,
                "AllSynonyms", Statements.SYNONYMS,
                whereClause % (self.schemasClause, self.schemasClause),
                Object.Synonym))

    def ExportTables(self):
        """Export all of the tables."""
        print >> sys.stderr, "Describing tables..."
        self.ExportObjects(Object.ObjectIterator(self.environment,
                "AllTables", Statements.TABLES,
                "where o.owner %s" % self.schemasClause, Object.Table))

    def ExportTriggers(self):
        """Export all of the triggers."""
        print >> sys.stderr, "Describing triggers..."
        self.ExportObjects(Object.ObjectIterator(self.environment,
                "AllTriggers", Statements.TRIGGERS,
                "where o.owner %s" % self.schemasClause, Object.Trigger))

    def ExportUsers(self):
        """Export all of the users."""
        print >> sys.stderr, "Describing users..."
        whereClause = "where o.username %s" % self.schemasClause
        self.ExportObjects(Object.ObjectIterator(self.environment, "AllUsers",
                Statements.USERS, whereClause, Object.User))

    def ExportViews(self):
        """Export all of the views (done in source by default)."""
        pass

    def ObjectIncluded(self, obj):
        """Return true if the object should be included in the export."""
        if self.includedObjects is None:
            return True
        if isinstance(obj, (Object.Constraint, Object.Index, Object.Trigger)):
            name = obj.tableName
        elif isinstance(obj, Object.Synonym):
            name = obj.objectName
        else:
            name = obj.name
        return (obj.owner, name) in self.includedObjects

    def RetrieveAndExportObject(self, objectOwner, objectName, objectType):
        """Retrieve and export the object."""
        if self.includedObjects is not None \
                and (objectOwner, objectName) not in self.includedObjects:
            return
        self.SetOwner(objectOwner, objectType)
        if self.nameOnly:
            if objectOwner == "PUBLIC":
                objectType = "%s %s" % (objectOwner, objectType)
            print >> self.outFile, objectName, "(%s)" % objectType
        else:
            object = Utils.ObjectByType(self.environment, objectOwner,
                    objectName, objectType)
            self.ExportObject(object)

    def RetrieveDependencies(self):
        """Retrieve the list of dependencies for source objects."""
        typesArg = self.SourceTypesClause()
        cursor, isPrepared = self.environment.Cursor()
        cursor.execute("""
                select
                  owner,
                  name,
                  type,
                  referenced_owner,
                  referenced_name,
                  referenced_type
                from %s_dependencies
                where referenced_owner %s
                  and owner %s
                  and referenced_link_name is null
                  and type in (%s)
                  and referenced_type in (%s)""" % \
                (self.environment.ViewPrefix(), self.schemasClause,
                 self.schemasClause, typesArg, typesArg))
        return cursor.fetchall()

    def RetrieveSourceObjects(self):
        """Retrieve the list of source objects to be exported."""
        cursor, isPrepared = self.environment.Cursor()
        cursor.execute("""
                select
                  owner,
                  object_name,
                  object_type
                from %s_objects
                where owner %s
                  and object_type in (%s)""" % \
                (self.environment.ViewPrefix(), self.schemasClause,
                 self.SourceTypesClause()))
        return cursor.fetchall()

    def SetOwner(self, objectOwner, objectType):
        """Set the current owner being exported."""
        if objectOwner != self.currentOwner and objectType != "PUBLIC SYNONYM":
            self.currentOwner = objectOwner
            if self.nameOnly:
                print >> self.outFile
            print >> self.outFile, "connect", self.currentOwner.lower()
            print >> self.outFile

    def SourceTypes(self):
        """Return the list of source types to be included in the output."""
        return [t for t in Utils.SOURCE_TYPES if self.TypeIncluded(t)]

    def SourceTypesClause(self):
        """Return the clause suitable for inclusion in a SQL statement to
           restrict the source types to be included in the output."""
        return ",".join(["'%s'" % t for t in self.SourceTypes()])

    def TypeIncluded(self, objectType):
        """Return true if the type is to be included in the output."""
        return not self.__objectTypes or objectType in self.__objectTypes


class Exporter(Describer):
    """Exports objects into one file per object."""

    def __init__(self, environment, options, baseDir):
        self.baseDir = baseDir
        if not os.path.exists(baseDir):
            os.makedirs(baseDir)
        self.splitRelated = False
        self.suppressOwnerDir = False
        self.exportLevel = 0
        self.dirs = {}
        Describer.__init__(self, environment, options)
        self.currentOwner = None

    def ExportObject(self, object):
        """Export the object into a new file."""
        if not self.exportLevel or self.splitRelated:
            ownerDir = self.currentOwner
            if self.suppressOwnerDir:
                ownerDir = ""
            fileName = FileNameForObject(self.baseDir, ownerDir, object.name,
                    object.type)
            dirName = os.path.dirname(fileName)
            if dirName not in self.dirs:
                if not os.path.exists(dirName):
                    os.makedirs(dirName)
                self.dirs[dirName] = None
            self.outFile = file(fileName, "w")
        self.exportLevel += 1
        Describer.ExportObject(self, object)
        self.exportLevel -= 1

    def ExportSource(self):
        """Export all of the source objects."""
        print >> sys.stderr, "Describing interdependent objects..."
        for owner, name, type in self.RetrieveSourceObjects():
            if type == "VIEW":
                continue
            if self.wantRelated and type in ("PACKAGE BODY", "TYPE BODY"):
                continue
            self.RetrieveAndExportObject(owner, name, type)

    def ExportViews(self):
        """Export all of the views."""
        print >> sys.stderr, "Describing views..."
        self.ExportObjects(Object.ObjectIterator(self.environment, "AllViews",
                Statements.VIEWS, "where o.owner %s" % self.schemasClause,
                Object.View))

    def SetOwner(self, owner, type):
        """Set the current owner being exported."""
        if owner != self.currentOwner:
            self.currentOwner = owner


def FileNameForObject(baseDir, owner, name, type):
    """Return the file name for the object."""
    return os.path.join(baseDir, owner.lower(), type.lower().replace(" ", "_"),
            name.lower() + ".sql")

