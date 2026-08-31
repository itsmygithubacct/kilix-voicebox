import { Download, Edit, Mic, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { CircleButton } from '@/components/ui/circle-button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useToast } from '@/components/ui/use-toast';
import { apiClient } from '@/lib/api/client';
import type { DeletePreview, VoiceProfileResponse } from '@/lib/api/types';
import { useDeleteProfile, useExportProfile } from '@/lib/hooks/useProfiles';
import { cn } from '@/lib/utils/cn';
import { useUIStore } from '@/stores/uiStore';

interface ProfileCardProps {
  profile: VoiceProfileResponse;
}

export function ProfileCard({ profile }: ProfileCardProps) {
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deletePreview, setDeletePreview] = useState<DeletePreview | null>(null);
  const [avatarError, setAvatarError] = useState(false);
  const deleteProfile = useDeleteProfile();
  const exportProfile = useExportProfile();
  const setEditingProfileId = useUIStore((state) => state.setEditingProfileId);
  const setProfileDialogOpen = useUIStore((state) => state.setProfileDialogOpen);
  const selectedProfileId = useUIStore((state) => state.selectedProfileId);
  const setSelectedProfileId = useUIStore((state) => state.setSelectedProfileId);
  const { toast } = useToast();

  const isSelected = selectedProfileId === profile.id;

  const avatarUrl = profile.avatar_path ? apiClient.getAvatarUrl(profile.id) : null;

  const handleSelect = () => {
    setSelectedProfileId(isSelected ? null : profile.id);
  };

  const handleEdit = () => {
    setEditingProfileId(profile.id);
    setProfileDialogOpen(true);
  };

  const handleDeleteClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const preview = await apiClient.getProfileDeletePreview(profile.id);
      setDeletePreview(preview);
      setDeleteDialogOpen(true);
    } catch (error) {
      toast({
        title: 'Could not preview deletion',
        description: error instanceof Error ? error.message : 'DELETE_PREVIEW_FAILED',
        variant: 'destructive',
      });
    }
  };

  const handleDeleteConfirm = () => {
    deleteProfile.mutate(profile.id);
    setDeleteDialogOpen(false);
    setDeletePreview(null);
  };

  const handleExport = (e: React.MouseEvent) => {
    e.stopPropagation();
    exportProfile.mutate(profile.id);
  };

  return (
    <>
      <Card
        className={cn(
          'cursor-pointer hover:shadow-md transition-all flex flex-col',
          isSelected && 'ring-2 ring-primary shadow-md',
        )}
        onClick={handleSelect}
      >
        <CardHeader className="p-3 pb-2">
          <CardTitle className="flex items-center gap-1.5 text-base font-medium">
            <div className="h-6 w-6 rounded-full bg-muted flex items-center justify-center shrink-0 overflow-hidden">
              {avatarUrl && !avatarError ? (
                <img
                  src={avatarUrl}
                  alt={`${profile.name} avatar`}
                  className={cn(
                    'h-full w-full object-cover transition-all duration-200',
                    !isSelected && 'grayscale',
                  )}
                  onError={() => setAvatarError(true)}
                />
              ) : (
                <Mic className="h-3.5 w-3.5 text-muted-foreground" />
              )}
            </div>
            <span className="break-words">{profile.name}</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-3 pt-0 flex flex-col flex-1">
          <p className="text-xs text-muted-foreground mb-1.5 line-clamp-2 leading-relaxed">
            {profile.description || 'No description'}
          </p>
          <div className="mb-2">
            <Badge variant="outline" className="text-xs h-5 px-1.5 text-muted-foreground">
              {profile.language}
            </Badge>
          </div>
          <div className="flex gap-0.5 justify-end items-end mt-auto">
            <CircleButton
              icon={Download}
              onClick={handleExport}
              disabled={exportProfile.isPending}
              aria-label="Export profile"
            />
            <CircleButton
              icon={Edit}
              onClick={(e) => {
                e.stopPropagation();
                handleEdit();
              }}
              aria-label="Edit profile"
            />
            <CircleButton
              icon={Trash2}
              onClick={handleDeleteClick}
              disabled={deleteProfile.isPending}
              aria-label="Delete profile"
            />
          </div>
        </CardContent>
      </Card>

      <Dialog
        open={deleteDialogOpen}
        onOpenChange={(open) => {
          setDeleteDialogOpen(open);
          if (!open) setDeletePreview(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Profile</DialogTitle>
            <DialogDescription>
              Move {deletePreview?.count ?? 0}/{deletePreview?.total ?? 0} owned entries for{' '}
              {profile.name} to private recoverable trash?
            </DialogDescription>
            {deletePreview && (
              <ul className="max-h-32 overflow-y-auto rounded border p-2 text-xs text-muted-foreground">
                {deletePreview.files.map((file) => (
                  <li key={file}>{file}</li>
                ))}
              </ul>
            )}
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setDeleteDialogOpen(false);
                setDeletePreview(null);
              }}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={deleteProfile.isPending}
            >
              {deleteProfile.isPending ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
